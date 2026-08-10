// Drake cluster agent.
//
// Outbound-only: enroll once with a one-time token (key generated locally,
// never leaves the process), then run read-only Kubernetes discovery over
// mTLS + proof-of-possession. The only listener is the loopback liveness
// probe. Misconfiguration refuses to start; nothing is guessed.
//
// `agent healthcheck [addr]` probes the loopback liveness endpoint and
// exits 0/1 — the container liveness command, with no shell required.
package main

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/Duosis-Developer-Team/Drake/apps/cluster-agent/internal/config"
	"github.com/Duosis-Developer-Team/Drake/apps/cluster-agent/internal/engine"
	"github.com/Duosis-Developer-Team/Drake/apps/cluster-agent/internal/enrollment"
	"github.com/Duosis-Developer-Team/Drake/apps/cluster-agent/internal/health"
	"github.com/Duosis-Developer-Team/Drake/apps/cluster-agent/internal/identity"
	"github.com/Duosis-Developer-Team/Drake/apps/cluster-agent/internal/kube"
	"github.com/Duosis-Developer-Team/Drake/apps/cluster-agent/internal/logging"
	"github.com/Duosis-Developer-Team/Drake/apps/cluster-agent/internal/transport"
)

// agentVersion is stamped by the build; the default marks dev builds.
var agentVersion = "0.4.0-dev"

// swappableSender lets certificate renewal rebuild the mTLS transport
// without restarting the sync engine.
type swappableSender struct {
	mu     sync.RWMutex
	client *transport.Client
}

func (s *swappableSender) Post(ctx context.Context, path string, payload any) (int, []byte, error) {
	s.mu.RLock()
	client := s.client
	s.mu.RUnlock()
	return client.Post(ctx, path, payload)
}

func (s *swappableSender) swap(client *transport.Client) {
	s.mu.Lock()
	s.client = client
	s.mu.Unlock()
}

// healthcheckMain implements the liveness subcommand: a bounded loopback
// probe with no shell, curl, or network beyond loopback.
func healthcheckMain(args []string) int {
	address := "127.0.0.1:8090"
	if len(args) > 0 && args[0] != "" {
		address = args[0]
	} else if fromEnv := os.Getenv("DRAKE_AGENT_HEALTH_LISTEN_ADDR"); fromEnv != "" {
		address = fromEnv
	}
	if !strings.HasPrefix(address, "127.0.0.1:") && !strings.HasPrefix(address, "[::1]:") &&
		!strings.HasPrefix(address, "localhost:") {
		fmt.Fprintln(os.Stderr, "healthcheck probes loopback only")
		return 1
	}
	client := &http.Client{Timeout: 2 * time.Second}
	response, err := client.Get("http://" + address + "/healthz")
	if err != nil {
		fmt.Fprintln(os.Stderr, "healthcheck: "+err.Error())
		return 1
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		fmt.Fprintf(os.Stderr, "healthcheck: status %d\n", response.StatusCode)
		return 1
	}
	return 0
}

func main() {
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		os.Exit(healthcheckMain(os.Args[2:]))
	}

	cfg := config.FromEnv()
	logger := logging.New(cfg.LogLevel, os.Stderr)

	if err := cfg.Validate(); err != nil {
		logger.Error("invalid configuration", "error", err.Error())
		os.Exit(2)
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	// The liveness probe answers from the FIRST moment the process is
	// alive: enrollment/backoff windows are alive-but-connecting states,
	// not liveness failures (the probe is process health, nothing more).
	var group sync.WaitGroup
	probe := health.New(cfg.HealthListenAddr)
	group.Add(1)
	go func() {
		defer group.Done()
		if err := probe.Start(ctx); err != nil && ctx.Err() == nil {
			logger.Error("liveness probe server failed", "error", err.Error())
			stop()
		}
	}()

	id, err := loadOrEnroll(ctx, cfg, logger)
	if err != nil {
		logger.Error("agent identity unavailable", "error", err.Error())
		os.Exit(1)
	}

	factory := func(candidate *identity.Identity) (engine.Sender, error) {
		return transport.New(cfg.APIBaseURL, cfg.ServerCAFile, candidate)
	}

	// Finish any renewal that was interrupted mid-activation: the server
	// may already trust ONLY the pending key.
	id = engine.ReconcilePendingRenewal(ctx, factory, cfg.StateDir, id, logger)

	client, err := transport.New(cfg.APIBaseURL, cfg.ServerCAFile, id)
	if err != nil {
		logger.Error("transport setup failed", "error", err.Error())
		os.Exit(1)
	}
	sender := &swappableSender{client: client}

	clients, err := kube.New(cfg.Kubeconfig)
	if err != nil {
		logger.Error("kubernetes client setup failed", "error", err.Error())
		os.Exit(1)
	}

	syncEngine, err := engine.New(engine.Options{
		ClusterID:         cfg.ClusterID,
		AgentID:           id.AgentID,
		AgentVersion:      agentVersion,
		Dynamic:           clients.Dynamic,
		Sender:            sender,
		CRDPresent:        clients.CRDPresent,
		Logger:            logger,
		HeartbeatInterval: time.Duration(cfg.HeartbeatSeconds) * time.Second,
		LoadSequence: func() int64 {
			return identity.LoadSequence(cfg.StateDir)
		},
		StoreSequence: func(value int64) error {
			return identity.StoreSequence(cfg.StateDir, value)
		},
	})
	if err != nil {
		logger.Error("engine setup failed", "error", err.Error())
		os.Exit(1)
	}

	logger.Info("agent started",
		"cluster", cfg.ClusterName,
		"agent_version", agentVersion,
		"mode", "read-only",
	)

	group.Add(2)
	go func() {
		defer group.Done()
		engine.RenewalLoop(ctx, factory, id, cfg.StateDir, logger, func(renewed *identity.Identity) {
			rebuilt, buildErr := transport.New(cfg.APIBaseURL, cfg.ServerCAFile, renewed)
			if buildErr != nil {
				logger.Error("transport rebuild after renewal failed", "error", buildErr.Error())
				return
			}
			sender.swap(rebuilt)
		})
	}()
	go func() {
		defer group.Done()
		if runErr := syncEngine.Run(ctx); runErr != nil && ctx.Err() == nil {
			logger.Error("sync engine stopped", "error", runErr.Error())
			stop()
		}
	}()

	group.Wait()
	logger.Info("agent stopped gracefully")
}

// loadOrEnroll restores a saved identity or performs first-run enrollment
// with the one-time token. The token file is read exactly once and its
// value never appears in logs.
func loadOrEnroll(ctx context.Context, cfg config.Config, logger *slog.Logger) (*identity.Identity, error) {
	if id, err := identity.LoadCurrent(cfg.StateDir); err == nil {
		logger.Info("existing identity loaded", "agent_id", id.AgentID)
		return id, nil
	}
	if cfg.EnrollmentTokenFile == "" {
		return nil, errNoIdentity
	}
	raw, err := os.ReadFile(cfg.EnrollmentTokenFile)
	if err != nil {
		return nil, err
	}
	token := strings.TrimSpace(string(raw))
	id, err := enrollment.Exchange(
		ctx, cfg.EnrollmentBaseURL, cfg.ServerCAFile, token, cfg.ClusterID, agentVersion, cfg.StateDir,
	)
	if err != nil {
		return nil, err
	}
	logger.Info("enrollment complete", "agent_id", id.AgentID)
	return id, nil
}

var errNoIdentity = errIdentity("no saved identity and no enrollment token file configured")

type errIdentity string

func (e errIdentity) Error() string { return string(e) }

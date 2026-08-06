// Drake cluster agent.
//
// Outbound-only: enroll once with a one-time token (key generated locally,
// never leaves the process), then run read-only Kubernetes discovery over
// mTLS + proof-of-possession. The only listener is the loopback liveness
// probe. Misconfiguration refuses to start; nothing is guessed.
package main

import (
	"context"
	"log/slog"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/Duosis-Developer-Team/Drake/apps/cluster-agent/internal/collector"
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

func main() {
	cfg := config.FromEnv()
	logger := logging.New(cfg.LogLevel, os.Stderr)

	if err := cfg.Validate(); err != nil {
		logger.Error("invalid configuration", "error", err.Error())
		os.Exit(2)
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	id, err := loadOrEnroll(ctx, cfg, logger)
	if err != nil {
		logger.Error("agent identity unavailable", "error", err.Error())
		os.Exit(1)
	}

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

	// The registry guard still vets the collection contract at startup.
	registry := collector.NewRegistry()
	_ = registry

	syncEngine, err := engine.New(engine.Options{
		ClusterID:         cfg.ClusterID,
		AgentID:           id.AgentID,
		AgentVersion:      agentVersion,
		Dynamic:           clients.Dynamic,
		Sender:            sender,
		CRDPresent:        clients.CRDPresent,
		Logger:            logger,
		HeartbeatInterval: time.Duration(cfg.HeartbeatSeconds) * time.Second,
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

	var group sync.WaitGroup
	group.Add(2)
	go func() {
		defer group.Done()
		engine.RenewalLoop(ctx, sender, id, cfg.StateDir, logger, func(renewed *identity.Identity) {
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

	probe := health.New(cfg.HealthListenAddr)
	if err := probe.Start(ctx); err != nil {
		logger.Error("liveness probe server failed", "error", err.Error())
		os.Exit(1)
	}
	group.Wait()
	logger.Info("agent stopped gracefully")
}

// loadOrEnroll restores a saved identity or performs first-run enrollment
// with the one-time token. The token file is read exactly once and its
// value never appears in logs.
func loadOrEnroll(ctx context.Context, cfg config.Config, logger *slog.Logger) (*identity.Identity, error) {
	if id, err := identity.Load(cfg.StateDir); err == nil {
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
		ctx, cfg.APIBaseURL, cfg.ServerCAFile, token, cfg.ClusterID, agentVersion, cfg.StateDir,
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

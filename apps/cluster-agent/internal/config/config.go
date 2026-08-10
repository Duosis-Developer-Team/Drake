// Package config provides typed, validated agent configuration.
//
// All values come from DRAKE_AGENT_* environment variables. Validation is
// strict: a misconfigured agent refuses to start instead of guessing.
package config

import (
	"fmt"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"

	"github.com/google/uuid"
)

// Config is the complete agent configuration.
type Config struct {
	// APIBaseURL is the outbound Drake API endpoint for an ENROLLED agent:
	// heartbeat, inventory and certificate renewal. HTTPS is required;
	// plaintext is tolerated only for loopback development targets.
	//
	// In production this is the mutual-TLS listener, and every call to it
	// presents this agent's client certificate.
	APIBaseURL string
	// EnrollmentBaseURL is where the ONE pre-certificate call goes.
	//
	// It exists because an agent enrolling for the first time has no client
	// certificate, and the listener that serves everything else demands one
	// during the handshake. Defaults to APIBaseURL, which is what a single
	// combined listener (local, test, CI) wants.
	EnrollmentBaseURL string
	// ClusterName identifies this cluster in Drake (DNS-safe label).
	ClusterName string
	// HealthListenAddr serves the liveness probe. Loopback-only by default;
	// this is a probe endpoint, never a control surface.
	HealthListenAddr string
	// LogLevel is one of debug|info|warn|error.
	LogLevel string
	// ClusterID is the Drake cluster UUID this agent reports for.
	ClusterID string
	// StateDir stores the enrolled identity (key/cert, 0600).
	StateDir string
	// ServerCAFile pins the internal listener's certificate authority.
	ServerCAFile string
	// EnrollmentTokenFile holds the one-time token; read only when no
	// identity exists yet, never logged.
	EnrollmentTokenFile string
	// Kubeconfig switches from in-cluster config to an explicit file —
	// local development and disposable test clusters only.
	Kubeconfig string
	// HeartbeatSeconds paces the liveness heartbeat (default 30; E2E uses
	// small values to observe disconnect transitions quickly).
	HeartbeatSeconds int
}

var clusterNamePattern = regexp.MustCompile(`^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$`)

// FromEnv builds a Config from environment variables with safe defaults.
func FromEnv() Config {
	cfg := Config{
		APIBaseURL:          os.Getenv("DRAKE_AGENT_API_BASE_URL"),
		EnrollmentBaseURL:   os.Getenv("DRAKE_AGENT_ENROLLMENT_BASE_URL"),
		ClusterName:         os.Getenv("DRAKE_AGENT_CLUSTER_NAME"),
		HealthListenAddr:    os.Getenv("DRAKE_AGENT_HEALTH_LISTEN_ADDR"),
		LogLevel:            os.Getenv("DRAKE_AGENT_LOG_LEVEL"),
		ClusterID:           os.Getenv("DRAKE_AGENT_CLUSTER_ID"),
		StateDir:            os.Getenv("DRAKE_AGENT_STATE_DIR"),
		ServerCAFile:        os.Getenv("DRAKE_AGENT_SERVER_CA_FILE"),
		EnrollmentTokenFile: os.Getenv("DRAKE_AGENT_ENROLLMENT_TOKEN_FILE"),
		Kubeconfig:          os.Getenv("DRAKE_AGENT_KUBECONFIG"),
	}
	if cfg.HealthListenAddr == "" {
		cfg.HealthListenAddr = "127.0.0.1:8090"
	}
	if cfg.LogLevel == "" {
		cfg.LogLevel = "info"
	}
	if cfg.EnrollmentBaseURL == "" {
		// One listener serves both surfaces unless told otherwise.
		cfg.EnrollmentBaseURL = cfg.APIBaseURL
	}
	if cfg.StateDir == "" {
		cfg.StateDir = "/var/lib/drake-agent"
	}
	cfg.HeartbeatSeconds = 30
	if raw := os.Getenv("DRAKE_AGENT_HEARTBEAT_SECONDS"); raw != "" {
		if parsed, err := strconv.Atoi(raw); err == nil {
			cfg.HeartbeatSeconds = parsed
		}
	}
	return cfg
}

// Validate rejects unsafe or incomplete configuration.
func (c Config) Validate() error {
	if c.APIBaseURL == "" {
		return fmt.Errorf("api base url is required")
	}
	parsed, err := url.Parse(c.APIBaseURL)
	if err != nil || parsed.Host == "" {
		return fmt.Errorf("api base url is not a valid URL")
	}
	switch parsed.Scheme {
	case "https":
		// Always acceptable.
	case "http":
		host := parsed.Hostname()
		if host != "127.0.0.1" && host != "localhost" && host != "::1" {
			return fmt.Errorf("plaintext http is only allowed for loopback development targets")
		}
	default:
		return fmt.Errorf("api base url must use https")
	}
	if parsed.User != nil {
		return fmt.Errorf("api base url must not embed credentials")
	}

	if c.ClusterName == "" {
		return fmt.Errorf("cluster name is required")
	}
	if !clusterNamePattern.MatchString(c.ClusterName) {
		return fmt.Errorf("cluster name must be a DNS-safe label")
	}

	if !strings.HasPrefix(c.HealthListenAddr, "127.0.0.1:") &&
		!strings.HasPrefix(c.HealthListenAddr, "localhost:") &&
		!strings.HasPrefix(c.HealthListenAddr, "[::1]:") {
		return fmt.Errorf("health listener must bind loopback; it is a probe, not a control port")
	}

	switch c.LogLevel {
	case "debug", "info", "warn", "error":
	default:
		return fmt.Errorf("log level must be one of debug|info|warn|error")
	}

	if c.ClusterID == "" {
		return fmt.Errorf("cluster id is required")
	}
	if _, err := uuid.Parse(c.ClusterID); err != nil {
		return fmt.Errorf("cluster id must be a UUID")
	}
	if c.ServerCAFile == "" {
		return fmt.Errorf("server ca file is required; the agent never dials an unpinned endpoint")
	}
	if c.StateDir == "" {
		return fmt.Errorf("state dir is required")
	}
	if c.HeartbeatSeconds < 1 || c.HeartbeatSeconds > 300 {
		return fmt.Errorf("heartbeat interval must be within [1, 300] seconds")
	}
	return nil
}

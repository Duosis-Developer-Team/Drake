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
	"strings"
)

// Config is the complete agent configuration.
type Config struct {
	// APIBaseURL is the outbound Drake API endpoint. HTTPS is required;
	// plaintext is tolerated only for loopback development targets.
	APIBaseURL string
	// ClusterName identifies this cluster in Drake (DNS-safe label).
	ClusterName string
	// HealthListenAddr serves the liveness probe. Loopback-only by default;
	// this is a probe endpoint, never a control surface.
	HealthListenAddr string
	// LogLevel is one of debug|info|warn|error.
	LogLevel string
}

var clusterNamePattern = regexp.MustCompile(`^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$`)

// FromEnv builds a Config from environment variables with safe defaults.
func FromEnv() Config {
	cfg := Config{
		APIBaseURL:       os.Getenv("DRAKE_AGENT_API_BASE_URL"),
		ClusterName:      os.Getenv("DRAKE_AGENT_CLUSTER_NAME"),
		HealthListenAddr: os.Getenv("DRAKE_AGENT_HEALTH_LISTEN_ADDR"),
		LogLevel:         os.Getenv("DRAKE_AGENT_LOG_LEVEL"),
	}
	if cfg.HealthListenAddr == "" {
		cfg.HealthListenAddr = "127.0.0.1:8090"
	}
	if cfg.LogLevel == "" {
		cfg.LogLevel = "info"
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
	return nil
}

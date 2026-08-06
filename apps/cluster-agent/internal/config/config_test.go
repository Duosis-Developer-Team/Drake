package config

import (
	"strings"
	"testing"
)

func valid() Config {
	return Config{
		APIBaseURL:       "https://drake-api.example.test",
		ClusterName:      "cluster-a",
		HealthListenAddr: "127.0.0.1:8090",
		LogLevel:         "info",
		ClusterID:        "5f0c9a4e-8f19-4a52-9d5e-2f6f5b3f9a11",
		StateDir:         "/var/lib/drake-agent",
		ServerCAFile:     "/etc/drake-agent/tls/server-ca.pem",
	}
}

func TestClusterIDMustBeUUID(t *testing.T) {
	cfg := valid()
	for _, bad := range []string{"", "not-a-uuid"} {
		cfg.ClusterID = bad
		if err := cfg.Validate(); err == nil {
			t.Fatalf("expected error for cluster id %q", bad)
		}
	}
}

func TestServerCAFileRequired(t *testing.T) {
	cfg := valid()
	cfg.ServerCAFile = ""
	if err := cfg.Validate(); err == nil {
		t.Fatal("expected error: the agent must never dial an unpinned endpoint")
	}
}

func TestValidConfigPasses(t *testing.T) {
	if err := valid().Validate(); err != nil {
		t.Fatalf("expected valid config, got %v", err)
	}
}

func TestAPIBaseURLIsRequired(t *testing.T) {
	cfg := valid()
	cfg.APIBaseURL = ""
	if err := cfg.Validate(); err == nil {
		t.Fatal("expected error for missing api base url")
	}
}

func TestPlaintextHTTPRejectedForNonLoopback(t *testing.T) {
	cfg := valid()
	cfg.APIBaseURL = "http://drake-api.example.test"
	if err := cfg.Validate(); err == nil {
		t.Fatal("expected error for plaintext non-loopback url")
	}
}

func TestPlaintextHTTPAllowedForLoopbackDev(t *testing.T) {
	cfg := valid()
	cfg.APIBaseURL = "http://127.0.0.1:8000"
	if err := cfg.Validate(); err != nil {
		t.Fatalf("expected loopback http to be allowed for development, got %v", err)
	}
}

func TestEmbeddedCredentialsRejected(t *testing.T) {
	cfg := valid()
	cfg.APIBaseURL = "https://user:fakepw@drake-api.example.test"
	err := cfg.Validate()
	if err == nil {
		t.Fatal("expected error for embedded credentials")
	}
	if strings.Contains(err.Error(), "fakepw") {
		t.Fatal("error message must not echo the credential value")
	}
}

func TestClusterNameShape(t *testing.T) {
	cfg := valid()
	for _, bad := range []string{"", "Has Spaces", "UPPER", "-leading", "trailing-"} {
		cfg.ClusterName = bad
		if err := cfg.Validate(); err == nil {
			t.Fatalf("expected error for cluster name %q", bad)
		}
	}
}

func TestHealthListenerMustBeLoopback(t *testing.T) {
	cfg := valid()
	cfg.HealthListenAddr = "0.0.0.0:8090"
	if err := cfg.Validate(); err == nil {
		t.Fatal("expected error: health listener must not bind non-loopback interfaces")
	}
}

func TestFromEnvDefaults(t *testing.T) {
	t.Setenv("DRAKE_AGENT_API_BASE_URL", "https://drake-api.example.test")
	t.Setenv("DRAKE_AGENT_CLUSTER_NAME", "cluster-a")
	t.Setenv("DRAKE_AGENT_CLUSTER_ID", "5f0c9a4e-8f19-4a52-9d5e-2f6f5b3f9a11")
	t.Setenv("DRAKE_AGENT_SERVER_CA_FILE", "/etc/drake-agent/tls/server-ca.pem")
	cfg := FromEnv()
	if cfg.StateDir != "/var/lib/drake-agent" {
		t.Fatalf("expected default state dir, got %q", cfg.StateDir)
	}
	if cfg.HealthListenAddr != "127.0.0.1:8090" {
		t.Fatalf("expected loopback default health addr, got %q", cfg.HealthListenAddr)
	}
	if cfg.LogLevel != "info" {
		t.Fatalf("expected default log level info, got %q", cfg.LogLevel)
	}
	if err := cfg.Validate(); err != nil {
		t.Fatalf("expected env config to validate, got %v", err)
	}
}

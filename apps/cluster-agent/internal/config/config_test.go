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
	cfg := FromEnv()
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

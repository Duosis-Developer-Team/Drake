// Drake cluster agent — Sprint 0 foundation.
//
// Starts config validation, structured logging, the loopback liveness probe,
// and graceful shutdown. There is deliberately no Kubernetes client and no
// cluster connection yet; the read-only collection contract lives in
// internal/collector and is enforced there.
package main

import (
	"context"
	"os"
	"os/signal"
	"syscall"

	"github.com/Duosis-Developer-Team/Drake/apps/cluster-agent/internal/collector"
	"github.com/Duosis-Developer-Team/Drake/apps/cluster-agent/internal/config"
	"github.com/Duosis-Developer-Team/Drake/apps/cluster-agent/internal/health"
	"github.com/Duosis-Developer-Team/Drake/apps/cluster-agent/internal/logging"
)

func main() {
	cfg := config.FromEnv()
	logger := logging.New(cfg.LogLevel, os.Stderr)

	if err := cfg.Validate(); err != nil {
		logger.Error("invalid configuration", "error", err.Error())
		os.Exit(2)
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	registry := collector.NewRegistry()
	logger.Info(
		"agent foundation started",
		"cluster", cfg.ClusterName,
		"collectors", len(registry.Names()),
		"mode", "read-only",
		"kubernetes_connection", "none (Sprint 0)",
	)

	probe := health.New(cfg.HealthListenAddr)
	if err := probe.Start(ctx); err != nil {
		logger.Error("liveness probe server failed", "error", err.Error())
		os.Exit(1)
	}
	logger.Info("agent stopped gracefully")
}

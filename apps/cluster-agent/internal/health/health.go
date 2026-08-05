// Package health serves the loopback-only liveness probe.
//
// This endpoint is a probe target for the kubelet, not a control surface:
// it binds loopback (or the pod-local address injected by the deployment),
// serves GET /healthz only, and accepts no input.
package health

import (
	"context"
	"net/http"
	"time"
)

// Server is a minimal liveness probe server.
type Server struct {
	server *http.Server
}

// New builds the probe server for addr (validated loopback-only by config).
func New(addr string) *Server {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"status":"alive"}`))
	})
	return &Server{
		server: &http.Server{
			Addr:              addr,
			Handler:           mux,
			ReadHeaderTimeout: 2 * time.Second,
			ReadTimeout:       5 * time.Second,
			WriteTimeout:      5 * time.Second,
		},
	}
}

// Handler exposes the mux for tests.
func (s *Server) Handler() http.Handler { return s.server.Handler }

// Start serves until the context is canceled, then shuts down gracefully.
func (s *Server) Start(ctx context.Context) error {
	errCh := make(chan error, 1)
	go func() {
		if err := s.server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			errCh <- err
		}
	}()
	select {
	case err := <-errCh:
		return err
	case <-ctx.Done():
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		return s.server.Shutdown(shutdownCtx)
	}
}

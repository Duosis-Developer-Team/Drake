// Package transport defines the outbound-only connection to the Drake API.
//
// The agent always dials out; nothing ever dials the agent. Sprint 0 provides
// the interface and a disconnected stub — real mTLS transport arrives with
// the enrollment sprint.
package transport

import (
	"context"
	"errors"
)

// ErrNotConnected is returned by the stub transport.
var ErrNotConnected = errors.New("transport not connected (foundation stub)")

// Batch is an opaque, bounded payload of normalized events.
type Batch struct {
	Events []any
}

// Transport sends observation batches to the Drake API (outbound only).
type Transport interface {
	// Send delivers one batch. Implementations must be bounded (timeout,
	// backoff) and must never log credentials.
	Send(ctx context.Context, batch Batch) error
	// Close releases resources.
	Close() error
}

// Disconnected is the Sprint 0 stub: it accepts no traffic and reports
// a typed error so callers degrade explicitly.
type Disconnected struct{}

func (Disconnected) Send(_ context.Context, _ Batch) error { return ErrNotConnected }
func (Disconnected) Close() error                          { return nil }

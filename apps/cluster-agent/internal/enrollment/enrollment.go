// Package enrollment defines how an agent obtains its service identity.
//
// Contract (implemented in a later sprint):
//   - The operator provisions a single-use, short-lived enrollment token.
//   - The agent exchanges it (outbound TLS) for a client certificate.
//   - The token is invalid after first use; certificates rotate.
//   - Tokens and keys never appear in logs (see internal/redact).
package enrollment

import (
	"context"
	"errors"
)

// ErrNotImplemented marks the Sprint 0 stub.
var ErrNotImplemented = errors.New("enrollment not implemented (foundation stub)")

// Credentials is a reference to the agent's obtained identity material.
// It intentionally never carries raw key bytes through logs or errors.
type Credentials struct {
	// CertificateRef points at where the certificate is stored (file path).
	CertificateRef string
	// KeyRef points at where the private key is stored (file path).
	KeyRef string
}

// Enroller exchanges a one-time token for agent credentials.
type Enroller interface {
	Exchange(ctx context.Context, oneTimeToken string) (Credentials, error)
}

// NotImplemented is the Sprint 0 stub.
type NotImplemented struct{}

func (NotImplemented) Exchange(_ context.Context, _ string) (Credentials, error) {
	return Credentials{}, ErrNotImplemented
}

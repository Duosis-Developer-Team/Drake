// Package transport is the outbound-only mTLS connection to the Drake API.
//
// The agent always dials out; nothing ever dials the agent. Every request:
// mTLS client identity, proof-of-possession headers, bounded timeouts and
// response bodies, no redirects, bounded retry with jittered backoff.
// Tokens, keys, and certificates are never logged.
package transport

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/rand"
	"net/http"
	"os"
	"time"

	"github.com/Duosis-Developer-Team/Drake/apps/cluster-agent/internal/identity"
)

const (
	maxResponseBytes = 1 << 20 // 1 MiB
	requestTimeout   = 15 * time.Second
	maxAttempts      = 5
)

// ErrReconcileRequired signals the server demands a full reconcile.
var ErrReconcileRequired = errors.New("server requires reconcile")

// Client is the authenticated agent transport.
type Client struct {
	base     string
	http     *http.Client
	identity *identity.Identity
}

// New builds an mTLS client for the internal agent API. serverCAFile pins
// the internal listener's certificate authority; verification is always on.
func New(base, serverCAFile string, id *identity.Identity) (*Client, error) {
	caPool := x509.NewCertPool()
	caPEM, err := os.ReadFile(serverCAFile)
	if err != nil {
		return nil, fmt.Errorf("read server ca: %w", err)
	}
	if !caPool.AppendCertsFromPEM(caPEM) {
		return nil, fmt.Errorf("server ca not parseable")
	}
	clientCert, err := tls.LoadX509KeyPair(id.CertPath, id.KeyPath)
	if err != nil {
		return nil, fmt.Errorf("load client identity: %w", err)
	}
	httpTransport := &http.Transport{
		TLSClientConfig: &tls.Config{
			RootCAs:      caPool,
			Certificates: []tls.Certificate{clientCert},
			MinVersion:   tls.VersionTLS12,
		},
	}
	return &Client{
		base: base,
		http: &http.Client{
			Timeout:   requestTimeout,
			Transport: httpTransport,
			// Redirects are never followed.
			CheckRedirect: func(*http.Request, []*http.Request) error {
				return http.ErrUseLastResponse
			},
		},
		identity: id,
	}, nil
}

// Post sends a JSON payload with PoP identity and bounded retries.
// A 409 with reconcile_required maps to ErrReconcileRequired.
func (c *Client) Post(ctx context.Context, path string, payload any) (int, []byte, error) {
	body, err := json.Marshal(payload)
	if err != nil {
		return 0, nil, fmt.Errorf("encode payload: %w", err)
	}
	var lastErr error
	for attempt := 0; attempt < maxAttempts; attempt++ {
		if attempt > 0 {
			backoff := time.Duration(1<<attempt) * 250 * time.Millisecond
			jitter := time.Duration(rand.Int63n(int64(backoff/2) + 1)) //nolint:gosec // jitter only
			select {
			case <-ctx.Done():
				return 0, nil, ctx.Err()
			case <-time.After(backoff + jitter):
			}
		}
		status, response, err := c.once(ctx, path, body)
		if err != nil {
			if ctx.Err() != nil {
				return 0, nil, ctx.Err()
			}
			lastErr = err
			continue
		}
		if status >= 500 {
			lastErr = fmt.Errorf("server error %d", status)
			continue
		}
		if status == http.StatusConflict && bytes.Contains(response, []byte("reconcile_required")) {
			return status, response, ErrReconcileRequired
		}
		return status, response, nil
	}
	return 0, nil, fmt.Errorf("request failed after %d attempts: %w", maxAttempts, lastErr)
}

func (c *Client) once(ctx context.Context, path string, body []byte) (int, []byte, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, c.base+path, bytes.NewReader(body))
	if err != nil {
		return 0, nil, err
	}
	request.Header.Set("Content-Type", "application/json")
	pop, err := c.identity.PopHeaders(http.MethodPost, path, body)
	if err != nil {
		return 0, nil, err
	}
	for name, value := range pop {
		request.Header.Set(name, value)
	}
	response, err := c.http.Do(request)
	if err != nil {
		return 0, nil, err
	}
	defer response.Body.Close()
	data, err := io.ReadAll(io.LimitReader(response.Body, maxResponseBytes))
	if err != nil {
		return 0, nil, err
	}
	return response.StatusCode, data, nil
}

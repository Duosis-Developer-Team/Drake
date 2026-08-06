package engine

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"math/rand"
	"net/http"
	"time"

	"github.com/Duosis-Developer-Team/Drake/apps/cluster-agent/internal/identity"
)

type renewalResponse struct {
	CertificatePEM      string `json:"certificate_pem"`
	CAChainPEM          string `json:"ca_chain_pem"`
	CertificateNotAfter string `json:"certificate_not_after"`
}

// RenewalLoop rotates the agent certificate at roughly two thirds of its
// lifetime (jittered so fleets never renew in lockstep). A fresh key is
// generated locally for every renewal; only the CSR travels. onRenewed lets
// the caller rebuild the mTLS transport with the new identity. Failures
// retry with backoff and fail closed: the agent never runs on an
// unverified identity.
func RenewalLoop(
	ctx context.Context,
	sender Sender,
	current *identity.Identity,
	stateDir string,
	logger *slog.Logger,
	onRenewed func(*identity.Identity),
) {
	id := current
	for ctx.Err() == nil {
		wait := renewalDelay(time.Now(), id.NotAfter)
		select {
		case <-ctx.Done():
			return
		case <-time.After(wait):
		}
		renewed, err := renewOnce(ctx, sender, id, stateDir)
		if err != nil {
			if ctx.Err() != nil {
				return
			}
			logger.Warn("certificate renewal failed; retrying", "error", err.Error())
			select {
			case <-ctx.Done():
				return
			case <-time.After(time.Minute):
			}
			continue
		}
		logger.Info("certificate renewed", "not_after", renewed.NotAfter.UTC().Format(time.RFC3339))
		id = renewed
		onRenewed(renewed)
	}
}

// renewalDelay targets 2/3 of the remaining lifetime with ±10% jitter and
// never sleeps below a floor once expiry is near.
func renewalDelay(now, notAfter time.Time) time.Duration {
	remaining := notAfter.Sub(now)
	if remaining <= 0 {
		return time.Second
	}
	target := remaining * 2 / 3
	jitterRange := int64(target / 10)
	if jitterRange > 0 {
		target += time.Duration(rand.Int63n(2*jitterRange) - jitterRange) //nolint:gosec // jitter only
	}
	if target < time.Second {
		target = time.Second
	}
	return target
}

func renewOnce(
	ctx context.Context, sender Sender, id *identity.Identity, stateDir string,
) (*identity.Identity, error) {
	key, err := identity.GenerateKey()
	if err != nil {
		return nil, fmt.Errorf("generate key: %w", err)
	}
	csr, err := identity.CSRPEM(key)
	if err != nil {
		return nil, err
	}
	status, body, err := sender.Post(
		ctx, "/internal/v1/agent/certificates/renew", map[string]any{"csr_pem": csr},
	)
	if err != nil {
		return nil, err
	}
	if status != http.StatusOK {
		return nil, fmt.Errorf("renewal refused with status %d", status)
	}
	var parsed renewalResponse
	if err := json.Unmarshal(body, &parsed); err != nil {
		return nil, fmt.Errorf("renewal response malformed: %w", err)
	}
	notAfter, err := time.Parse(time.RFC3339, parsed.CertificateNotAfter)
	if err != nil {
		return nil, fmt.Errorf("renewal expiry malformed: %w", err)
	}
	return identity.Save(stateDir, id.AgentID, key, parsed.CertificatePEM, parsed.CAChainPEM, notAfter)
}

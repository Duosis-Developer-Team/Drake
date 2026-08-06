package engine

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"math/rand"
	"net/http"
	"time"

	"github.com/google/uuid"

	"github.com/Duosis-Developer-Team/Drake/apps/cluster-agent/internal/identity"
)

type renewalResponse struct {
	CertificatePEM      string `json:"certificate_pem"`
	CAChainPEM          string `json:"ca_chain_pem"`
	CertificateNotAfter string `json:"certificate_not_after"`
}

// SenderFactory builds an authenticated transport for a given identity —
// renewal needs one for the CURRENT key (prepare) and one for the PENDING
// key (activation proof).
type SenderFactory func(*identity.Identity) (Sender, error)

// ReconcilePendingRenewal finishes an interrupted renewal at startup: if a
// prepared bundle exists on disk, activation (signed with the pending key)
// is the single reconciliation point — the server promotes if it had not,
// or acknowledges idempotently if it already had. On explicit refusal the
// pending bundle is discarded and the current identity continues; on
// transport failure the current identity continues and the next renewal
// cycle retries from scratch.
func ReconcilePendingRenewal(
	ctx context.Context,
	factory SenderFactory,
	stateDir string,
	current *identity.Identity,
	logger *slog.Logger,
) *identity.Identity {
	bundleID, renewalID, ok := identity.PendingRenewal(stateDir)
	if !ok {
		return current
	}
	pending, err := identity.LoadBundle(stateDir, bundleID)
	if err != nil {
		logger.Warn("pending renewal bundle unreadable; discarding", "error", err.Error())
		identity.ClearPending(stateDir)
		return current
	}
	promoted, err := activate(ctx, factory, pending, renewalID)
	if err != nil {
		logger.Warn("pending renewal activation failed; keeping current identity",
			"error", err.Error())
		return current
	}
	if !promoted {
		identity.ClearPending(stateDir)
		return current
	}
	if err := identity.Promote(stateDir, bundleID); err != nil {
		logger.Error("promoting activated bundle failed", "error", err.Error())
		return current
	}
	identity.ClearPending(stateDir)
	logger.Info("interrupted renewal completed at startup")
	return pending
}

// RenewalLoop rotates the certificate at roughly two thirds of its
// lifetime (jittered). Each renewal is two-phase and idempotent:
// prepare (CSR under the CURRENT key) → atomic bundle save → activation
// proof (signed with the NEW key) → atomic local promotion. The current
// key keeps working until activation, so a lost response at any step
// leaves a working agent. Fresh keys every time; failures retry.
func RenewalLoop(
	ctx context.Context,
	factory SenderFactory,
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
		renewed, err := renewOnce(ctx, factory, id, stateDir)
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
	ctx context.Context, factory SenderFactory, id *identity.Identity, stateDir string,
) (*identity.Identity, error) {
	// PREPARE: fresh key, CSR only; signed with the CURRENT key.
	key, err := identity.GenerateKey()
	if err != nil {
		return nil, fmt.Errorf("generate key: %w", err)
	}
	csr, err := identity.CSRPEM(key)
	if err != nil {
		return nil, err
	}
	renewalID := uuid.NewString()
	currentSender, err := factory(id)
	if err != nil {
		return nil, err
	}
	status, body, err := currentSender.Post(
		ctx, "/internal/v1/agent/certificates/renew",
		map[string]any{"renewal_id": renewalID, "csr_pem": csr},
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

	// SAVE the complete bundle first (inert until pointed at), THEN record
	// the pending marker — a crash in between costs nothing.
	bundleID, err := identity.SaveBundle(
		stateDir, id.AgentID, key, parsed.CertificatePEM, parsed.CAChainPEM, notAfter,
	)
	if err != nil {
		return nil, fmt.Errorf("save pending bundle: %w", err)
	}
	if err := identity.SetPending(stateDir, bundleID, renewalID); err != nil {
		return nil, fmt.Errorf("record pending renewal: %w", err)
	}
	pending, err := identity.LoadBundle(stateDir, bundleID)
	if err != nil {
		return nil, fmt.Errorf("reload pending bundle: %w", err)
	}

	// ACTIVATE: possession of the new key is the promotion proof.
	promoted, err := activate(ctx, factory, pending, renewalID)
	if err != nil {
		return nil, err
	}
	if !promoted {
		identity.ClearPending(stateDir)
		return nil, fmt.Errorf("activation refused")
	}
	if err := identity.Promote(stateDir, bundleID); err != nil {
		return nil, fmt.Errorf("promote bundle: %w", err)
	}
	identity.ClearPending(stateDir)
	return pending, nil
}

func activate(
	ctx context.Context, factory SenderFactory, pending *identity.Identity, renewalID string,
) (bool, error) {
	sender, err := factory(pending)
	if err != nil {
		return false, err
	}
	status, _, err := sender.Post(
		ctx, "/internal/v1/agent/certificates/activate",
		map[string]any{"renewal_id": renewalID},
	)
	if err != nil {
		return false, err
	}
	if status == http.StatusOK {
		return true, nil
	}
	return false, nil
}

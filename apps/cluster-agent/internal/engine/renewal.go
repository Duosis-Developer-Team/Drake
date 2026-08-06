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

// Activation-retry backoff bounds for ambiguous transport failures.
// Package vars so tests can shrink them; production never mutates these.
var (
	activationRetryBase = 5 * time.Second
	activationRetryMax  = time.Minute
)

// ReconcilePendingRenewal finishes an interrupted renewal at startup with
// ONE activation attempt: the server promotes if it had not, or
// acknowledges idempotently if it already had. On explicit refusal the
// pending bundle is discarded; on an ambiguous transport failure the
// pending survives untouched — RenewalLoop's settlePending keeps
// reconciling it in process with bounded backoff.
func ReconcilePendingRenewal(
	ctx context.Context,
	factory SenderFactory,
	stateDir string,
	current *identity.Identity,
	logger *slog.Logger,
) *identity.Identity {
	settled, outcome := tryActivatePending(ctx, factory, stateDir, logger)
	switch outcome {
	case pendingPromoted:
		logger.Info("interrupted renewal completed at startup")
		return settled
	case pendingAmbiguous:
		logger.Warn("pending renewal activation ambiguous at startup; " +
			"the renewal loop keeps reconciling it in process")
	}
	return current
}

type pendingOutcome int

const (
	pendingNone pendingOutcome = iota
	pendingPromoted
	pendingRefused
	pendingAmbiguous
)

// tryActivatePending makes ONE activation attempt for a pending renewal.
// Explicit refusal discards the pending (the new key is NEVER assumed
// current without the server's word); an ambiguous transport failure
// keeps the SAME bundle and renewal id on disk for the next attempt.
func tryActivatePending(
	ctx context.Context, factory SenderFactory, stateDir string, logger *slog.Logger,
) (*identity.Identity, pendingOutcome) {
	bundleID, renewalID, ok := identity.PendingRenewal(stateDir)
	if !ok {
		return nil, pendingNone
	}
	pending, err := identity.LoadBundle(stateDir, bundleID)
	if err != nil {
		logger.Warn("pending renewal bundle unreadable; discarding", "error", err.Error())
		identity.ClearPending(stateDir)
		return nil, pendingRefused
	}
	promoted, err := activate(ctx, factory, pending, renewalID)
	if err != nil {
		// Ambiguous: the server may or may not have committed. The pending
		// material and renewal id MUST survive for the idempotent retry.
		return nil, pendingAmbiguous
	}
	if !promoted {
		identity.ClearPending(stateDir)
		return nil, pendingRefused
	}
	if err := identity.Promote(stateDir, bundleID); err != nil {
		logger.Error("promoting activated bundle failed", "error", err.Error())
		return nil, pendingAmbiguous
	}
	identity.ClearPending(stateDir)
	return pending, pendingPromoted
}

// settlePending reconciles any pending renewal IN PROCESS before the loop
// may sleep or start a new renewal. The dangerous race this closes: the
// server committed the activation but every response was lost — the old
// key is already dead server-side, so waiting for a restart would leave
// the agent locked out while liveness stays green. Ambiguous failures
// retry the SAME renewal id and bundle with bounded, context-aware
// backoff; unreachable servers never cost the pending material; explicit
// refusals discard it without ever assuming the new key.
func settlePending(
	ctx context.Context,
	factory SenderFactory,
	stateDir string,
	current *identity.Identity,
	logger *slog.Logger,
	onRenewed func(*identity.Identity),
) *identity.Identity {
	backoff := activationRetryBase
	for ctx.Err() == nil {
		settled, outcome := tryActivatePending(ctx, factory, stateDir, logger)
		switch outcome {
		case pendingNone, pendingRefused:
			return current
		case pendingPromoted:
			logger.Info("pending renewal reconciled in process",
				"not_after", settled.NotAfter.UTC().Format(time.RFC3339))
			onRenewed(settled)
			return settled
		case pendingAmbiguous:
			logger.Warn("activation ambiguous; retrying the same renewal id",
				"backoff", backoff.String())
			select {
			case <-ctx.Done():
				return current
			case <-time.After(backoff):
			}
			backoff = min(backoff*2, activationRetryMax)
		}
	}
	return current
}

// RenewalLoop rotates the certificate at roughly two thirds of its
// lifetime (jittered). Each renewal is two-phase and idempotent:
// prepare (CSR under the CURRENT key) → atomic bundle save → activation
// proof (signed with the NEW key) → atomic local promotion. Before any
// sleep or new renewal, settlePending finishes whatever is on disk — no
// new renewal ever starts while a pending one exists, and an ambiguous
// activation is reconciled in process without a restart.
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
		id = settlePending(ctx, factory, stateDir, id, logger, onRenewed)
		if ctx.Err() != nil {
			return
		}
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
			// With a pending on disk, settlePending owns the backoff at the
			// top of the loop; pause only when there is nothing to settle
			// (e.g. the prepare itself was refused) to avoid a tight loop.
			if _, _, pendingExists := identity.PendingRenewal(stateDir); !pendingExists {
				select {
				case <-ctx.Done():
					return
				case <-time.After(time.Minute):
				}
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

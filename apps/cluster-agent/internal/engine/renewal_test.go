package engine

import (
	"context"
	"crypto/ecdsa"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"log/slog"
	"math/big"
	"os"
	"testing"
	"time"

	"github.com/Duosis-Developer-Team/Drake/apps/cluster-agent/internal/identity"
)

func testLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError}))
}

func selfSigned(t *testing.T, key *ecdsa.PrivateKey) string {
	t.Helper()
	template := &x509.Certificate{
		SerialNumber: big.NewInt(1),
		Subject:      pkix.Name{CommonName: "renewal-test"},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().Add(time.Hour),
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		t.Fatalf("self-sign: %v", err)
	}
	return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}))
}

func seededIdentity(t *testing.T, dir string) *identity.Identity {
	t.Helper()
	key, err := identity.GenerateKey()
	if err != nil {
		t.Fatalf("keygen: %v", err)
	}
	bundleID, err := identity.SaveBundle(
		dir, "6a1d8b5f-9e2a-4b63-8c7d-3a5b6c7d8e9f", key,
		selfSigned(t, key), "ca", time.Now().Add(time.Hour),
	)
	if err != nil {
		t.Fatalf("save: %v", err)
	}
	if err := identity.Promote(dir, bundleID); err != nil {
		t.Fatalf("promote: %v", err)
	}
	current, err := identity.LoadCurrent(dir)
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	return current
}

// renewalServer fakes the prepare/activate endpoints per identity.
type renewalServer struct {
	t              *testing.T
	prepareStatus  int
	prepareErr     error
	activateStatus int
	activateErr    error
	prepares       int
	activations    int
	lastRenewalID  string
}

type boundSender struct {
	server *renewalServer
	id     *identity.Identity
}

func (b *boundSender) Post(_ context.Context, path string, payload any) (int, []byte, error) {
	body := payload.(map[string]any)
	switch path {
	case "/internal/v1/agent/certificates/renew":
		b.server.prepares++
		b.server.lastRenewalID = body["renewal_id"].(string)
		if b.server.prepareErr != nil {
			return 0, nil, b.server.prepareErr
		}
		if b.server.prepareStatus != 200 {
			return b.server.prepareStatus, []byte("{}"), nil
		}
		// Issue a throwaway certificate for the CSR's key: content only
		// needs to parse; possession semantics live server-side.
		key, _ := identity.GenerateKey()
		response, _ := json.Marshal(map[string]string{
			"certificate_pem":       selfSigned(b.server.t, key),
			"ca_chain_pem":          "ca",
			"certificate_not_after": time.Now().Add(time.Hour).UTC().Format(time.RFC3339),
		})
		return 200, response, nil
	case "/internal/v1/agent/certificates/activate":
		b.server.activations++
		if b.server.activateErr != nil {
			return 0, nil, b.server.activateErr
		}
		return b.server.activateStatus, []byte("{}"), nil
	default:
		return 0, nil, fmt.Errorf("unexpected path %s", path)
	}
}

func factoryFor(server *renewalServer) SenderFactory {
	return func(id *identity.Identity) (Sender, error) {
		return &boundSender{server: server, id: id}, nil
	}
}

func TestRenewOncePromotesOnlyAfterActivation(t *testing.T) {
	dir := t.TempDir()
	current := seededIdentity(t, dir)
	server := &renewalServer{t: t, prepareStatus: 200, activateStatus: 200}

	renewed, err := renewOnce(context.Background(), factoryFor(server), current, dir)
	if err != nil {
		t.Fatalf("renew: %v", err)
	}
	if renewed.Key().Equal(current.Key()) {
		t.Fatal("renewal must produce a FRESH key")
	}
	promoted, err := identity.LoadCurrent(dir)
	if err != nil || !promoted.Key().Equal(renewed.Key()) {
		t.Fatalf("activated bundle must be promoted: %v", err)
	}
	if _, _, pending := identity.PendingRenewal(dir); pending {
		t.Fatal("pending marker must clear after activation")
	}
	if server.prepares != 1 || server.activations != 1 {
		t.Fatalf("expected 1 prepare + 1 activate, got %d/%d", server.prepares, server.activations)
	}
}

func TestLostActivationResponseKeepsOldIdentityUntilReconcile(t *testing.T) {
	dir := t.TempDir()
	current := seededIdentity(t, dir)
	server := &renewalServer{
		t: t, prepareStatus: 200, activateErr: errors.New("network lost"),
	}

	_, err := renewOnce(context.Background(), factoryFor(server), current, dir)
	if err == nil {
		t.Fatal("lost activation must surface as an error")
	}
	// The OLD identity keeps working; the pending bundle survives on disk.
	still, loadErr := identity.LoadCurrent(dir)
	if loadErr != nil || !still.Key().Equal(current.Key()) {
		t.Fatalf("old identity must remain active: %v", loadErr)
	}
	if _, _, pending := identity.PendingRenewal(dir); !pending {
		t.Fatal("pending bundle must survive a lost activation")
	}

	// Restart: activation is the reconciliation point (idempotent server
	// side). Success promotes the SAME pending bundle.
	server.activateErr = nil
	server.activateStatus = 200
	reconciled := ReconcilePendingRenewal(
		context.Background(), factoryFor(server), dir, still, testLogger(),
	)
	if reconciled.Key().Equal(current.Key()) {
		t.Fatal("reconciliation must promote the pending identity")
	}
	if _, _, pending := identity.PendingRenewal(dir); pending {
		t.Fatal("pending marker must clear after reconciliation")
	}
}

func TestRefusedActivationDiscardsPendingAndKeepsCurrent(t *testing.T) {
	dir := t.TempDir()
	current := seededIdentity(t, dir)
	server := &renewalServer{t: t, prepareStatus: 200, activateStatus: 403}

	_, err := renewOnce(context.Background(), factoryFor(server), current, dir)
	if err == nil {
		t.Fatal("refused activation must surface as an error")
	}
	if _, _, pending := identity.PendingRenewal(dir); pending {
		t.Fatal("refused pending renewal must be discarded")
	}
	still, loadErr := identity.LoadCurrent(dir)
	if loadErr != nil || !still.Key().Equal(current.Key()) {
		t.Fatalf("current identity must keep working after refusal: %v", loadErr)
	}
}

func TestPrepareFailureLeavesNoPendingState(t *testing.T) {
	dir := t.TempDir()
	current := seededIdentity(t, dir)
	server := &renewalServer{t: t, prepareStatus: 403}
	_, err := renewOnce(context.Background(), factoryFor(server), current, dir)
	if err == nil {
		t.Fatal("refused prepare must surface as an error")
	}
	if _, _, pending := identity.PendingRenewal(dir); pending {
		t.Fatal("a refused prepare must leave no pending marker")
	}
	if server.activations != 0 {
		t.Fatal("no activation may happen without a prepared bundle")
	}
}

func TestReconcileWithRefusedPendingFallsBackToCurrent(t *testing.T) {
	dir := t.TempDir()
	current := seededIdentity(t, dir)
	// Manually plant a pending renewal (simulating a crash), then have the
	// server refuse it (e.g., pending expired server-side).
	key, _ := identity.GenerateKey()
	bundleID, err := identity.SaveBundle(
		dir, current.AgentID, key, selfSigned(t, key), "ca", time.Now().Add(time.Hour),
	)
	if err != nil {
		t.Fatalf("save: %v", err)
	}
	if err := identity.SetPending(dir, bundleID, "renewal-x"); err != nil {
		t.Fatalf("pending: %v", err)
	}
	server := &renewalServer{t: t, activateStatus: 403}
	reconciled := ReconcilePendingRenewal(
		context.Background(), factoryFor(server), dir, current, testLogger(),
	)
	if !reconciled.Key().Equal(current.Key()) {
		t.Fatal("refused pending must fall back to the current identity")
	}
	if _, _, pending := identity.PendingRenewal(dir); pending {
		t.Fatal("refused pending must be discarded")
	}
}

package engine

import (
	"bytes"
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
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Duosis-Developer-Team/Drake/apps/cluster-agent/internal/identity"
)

func testLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(&bytes.Buffer{}, nil))
}

// capturedLogger records everything for leak assertions.
func capturedLogger() (*slog.Logger, *bytes.Buffer) {
	buffer := &bytes.Buffer{}
	return slog.New(slog.NewTextHandler(buffer, &slog.HandlerOptions{Level: slog.LevelDebug})), buffer
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

// statefulServer models the REAL server key state: which public key it
// currently accepts, which pending key/renewal id exists, and — the crux —
// activations that COMMIT server-side while every response is lost.
type statefulServer struct {
	t  *testing.T
	mu sync.Mutex

	acceptedKey      *ecdsa.PublicKey // the key the server trusts right now
	pendingKey       *ecdsa.PublicKey
	pendingRenewalID string
	lastRenewalID    string // survives promotion for idempotent activation

	// dropActivationResponses: for this many activation calls the server
	// COMMITS the promotion (when applicable) but the response is lost.
	dropActivationResponses int
	refuseActivations       bool // explicit 403 path
	refusePrepares          bool

	prepares    int
	activations int
}

func keysEqual(a, b *ecdsa.PublicKey) bool {
	return a != nil && b != nil && a.Equal(b)
}

type stateBoundSender struct {
	server *statefulServer
	id     *identity.Identity
}

func (s *stateBoundSender) Post(_ context.Context, path string, payload any) (int, []byte, error) {
	server := s.server
	server.mu.Lock()
	defer server.mu.Unlock()
	body := payload.(map[string]any)
	callerKey := &s.id.Key().PublicKey
	switch path {
	case "/internal/v1/agent/certificates/renew":
		server.prepares++
		if server.refusePrepares || !keysEqual(callerKey, server.acceptedKey) {
			return 403, []byte("{}"), nil // old key dead after promotion
		}
		csrBlock, _ := pem.Decode([]byte(body["csr_pem"].(string)))
		request, err := x509.ParseCertificateRequest(csrBlock.Bytes)
		if err != nil {
			server.t.Fatalf("fake server: bad CSR: %v", err)
		}
		newKey, ok := request.PublicKey.(*ecdsa.PublicKey)
		if !ok {
			server.t.Fatal("fake server: CSR key is not ECDSA")
		}
		server.pendingKey = newKey
		server.pendingRenewalID = body["renewal_id"].(string)
		throwaway, _ := identity.GenerateKey()
		response, _ := json.Marshal(map[string]string{
			"certificate_pem":       selfSigned(server.t, throwaway),
			"ca_chain_pem":          "ca",
			"certificate_not_after": time.Now().Add(time.Hour).UTC().Format(time.RFC3339),
		})
		return 200, response, nil
	case "/internal/v1/agent/certificates/activate":
		server.activations++
		renewalID := body["renewal_id"].(string)
		if server.refuseActivations {
			return 403, []byte("{}"), nil
		}
		promotedNow := false
		if server.pendingKey != nil && renewalID == server.pendingRenewalID &&
			keysEqual(callerKey, server.pendingKey) {
			// COMMIT: from this instant the OLD key is dead server-side.
			server.acceptedKey = server.pendingKey
			server.lastRenewalID = server.pendingRenewalID
			server.pendingKey = nil
			server.pendingRenewalID = ""
			promotedNow = true
		}
		idempotentAck := !promotedNow && renewalID == server.lastRenewalID &&
			keysEqual(callerKey, server.acceptedKey)
		if !promotedNow && !idempotentAck {
			return 403, []byte("{}"), nil
		}
		if server.dropActivationResponses > 0 {
			server.dropActivationResponses--
			// The promotion above already happened; only the RESPONSE dies.
			return 0, nil, errors.New("transport: response lost")
		}
		return 200, []byte(`{"result":"activated"}`), nil
	default:
		return 0, nil, fmt.Errorf("unexpected path %s", path)
	}
}

func (s *statefulServer) factory() SenderFactory {
	return func(id *identity.Identity) (Sender, error) {
		return &stateBoundSender{server: s, id: id}, nil
	}
}

func (s *statefulServer) accepts(id *identity.Identity) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return keysEqual(&id.Key().PublicKey, s.acceptedKey)
}

func shrinkBackoff(t *testing.T) {
	t.Helper()
	oldBase, oldMax := activationRetryBase, activationRetryMax
	activationRetryBase, activationRetryMax = 10*time.Millisecond, 40*time.Millisecond
	t.Cleanup(func() { activationRetryBase, activationRetryMax = oldBase, oldMax })
}

func TestRenewOncePromotesOnlyAfterActivation(t *testing.T) {
	dir := t.TempDir()
	current := seededIdentity(t, dir)
	server := &statefulServer{t: t, acceptedKey: &current.Key().PublicKey}

	renewed, err := renewOnce(context.Background(), server.factory(), current, dir)
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
	if !server.accepts(renewed) || server.accepts(current) {
		t.Fatal("server must accept ONLY the new key after activation")
	}
}

func TestActivationCommittedButAllResponsesLostRecoversWithoutRestart(t *testing.T) {
	shrinkBackoff(t)
	dir := t.TempDir()
	oldIdentity := seededIdentity(t, dir)
	// The first TWO activation calls COMMIT/ack server-side but their
	// responses are lost — consecutive ambiguous failures.
	server := &statefulServer{
		t:                       t,
		acceptedKey:             &oldIdentity.Key().PublicKey,
		dropActivationResponses: 2,
	}
	logger, logs := capturedLogger()

	// The in-loop renewal hits the ambiguous window.
	_, err := renewOnce(context.Background(), server.factory(), oldIdentity, dir)
	if err == nil {
		t.Fatal("lost activation response must surface as an error")
	}

	// SERVER-SIDE the promotion is already real: the old key is dead.
	if server.accepts(oldIdentity) {
		t.Fatal("server must have promoted the new key (old key dead)")
	}
	oldKeySender, _ := server.factory()(oldIdentity)
	status, _, _ := oldKeySender.Post(
		context.Background(), "/internal/v1/agent/certificates/renew",
		map[string]any{"renewal_id": "probe", "csr_pem": mustCSR(t)},
	)
	if status != 403 {
		t.Fatalf("old key must be refused after server promotion, got %d", status)
	}
	// Locally nothing was promoted yet; the pending bundle survived.
	still, loadErr := identity.LoadCurrent(dir)
	if loadErr != nil || !still.Key().Equal(oldIdentity.Key()) {
		t.Fatalf("local current pointer must still be the old identity: %v", loadErr)
	}
	pendingBundle, pendingRenewalID, ok := identity.PendingRenewal(dir)
	if !ok {
		t.Fatal("pending bundle and renewal id must survive the ambiguous failure")
	}

	// NO restart: the running RenewalLoop reconciles in process.
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	swapped := make(chan *identity.Identity, 1)
	loopDone := make(chan struct{})
	go func() {
		defer close(loopDone)
		RenewalLoop(ctx, server.factory(), oldIdentity, dir, logger,
			func(renewed *identity.Identity) { swapped <- renewed })
	}()

	var reconciled *identity.Identity
	select {
	case reconciled = <-swapped:
	case <-time.After(10 * time.Second):
		t.Fatal("in-process reconciliation never completed")
	}
	cancel()
	select {
	case <-loopDone:
	case <-time.After(5 * time.Second):
		t.Fatal("RenewalLoop leaked after cancellation")
	}

	// Same renewal id, same bundle, ONE prepare total (never a new CSR).
	if server.prepares != 1+1 { // renewOnce prepare + the old-key probe
		t.Fatalf("no new prepare may happen during reconciliation, got %d", server.prepares)
	}
	nowBundle, _, stillPending := identity.PendingRenewal(dir)
	if stillPending {
		t.Fatal("pending marker must clear after reconciliation")
	}
	_ = nowBundle
	promoted, err := identity.LoadCurrent(dir)
	if err != nil || !promoted.Key().Equal(reconciled.Key()) {
		t.Fatalf("local current pointer must switch to the reconciled bundle: %v", err)
	}
	if promoted.Key().Equal(oldIdentity.Key()) {
		t.Fatal("reconciled identity must be the NEW key")
	}
	// The reconciled bundle IS the surviving pending bundle (same id).
	fromPending, err := identity.LoadBundle(dir, pendingBundle)
	if err != nil || !fromPending.Key().Equal(promoted.Key()) {
		t.Fatalf("promotion must use the SAME pending bundle %s: %v", pendingBundle, err)
	}
	_ = pendingRenewalID
	// New key accepted, old key refused; the swap callback fed the sender.
	if !server.accepts(promoted) || server.accepts(oldIdentity) {
		t.Fatal("server must accept the new key and refuse the old one")
	}
	// No private material anywhere in logs.
	if strings.Contains(logs.String(), "PRIVATE KEY") {
		t.Fatal("private material leaked into logs")
	}
}

func mustCSR(t *testing.T) string {
	t.Helper()
	key, err := identity.GenerateKey()
	if err != nil {
		t.Fatalf("keygen: %v", err)
	}
	csr, err := identity.CSRPEM(key)
	if err != nil {
		t.Fatalf("csr: %v", err)
	}
	return csr
}

func TestActivationNeverReachedServerKeepsOldIdentityAndRetries(t *testing.T) {
	shrinkBackoff(t)
	dir := t.TempDir()
	current := seededIdentity(t, dir)
	server := &statefulServer{t: t, acceptedKey: &current.Key().PublicKey}
	factory := server.factory()

	// Wrap the factory so activation calls NEVER reach the server for a
	// while (pure transport failure; nothing commits).
	var reachable sync.Map
	reachable.Store("down", true)
	blockingFactory := func(id *identity.Identity) (Sender, error) {
		inner, _ := factory(id)
		return senderFunc(func(ctx context.Context, path string, payload any) (int, []byte, error) {
			if down, _ := reachable.Load("down"); down.(bool) &&
				strings.HasSuffix(path, "/activate") {
				return 0, nil, errors.New("transport: connection refused")
			}
			return inner.Post(ctx, path, payload)
		}), nil
	}

	_, err := renewOnce(context.Background(), blockingFactory, current, dir)
	if err == nil {
		t.Fatal("unreachable activation must surface as an error")
	}
	// Nothing committed: the OLD identity keeps working server-side.
	if !server.accepts(current) {
		t.Fatal("old key must remain accepted when activation never arrived")
	}
	_, renewalIDBefore, ok := identity.PendingRenewal(dir)
	if !ok {
		t.Fatal("pending must survive an unreachable server")
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	swapped := make(chan *identity.Identity, 1)
	loopDone := make(chan struct{})
	go func() {
		defer close(loopDone)
		RenewalLoop(ctx, blockingFactory, current, dir, testLogger(),
			func(renewed *identity.Identity) { swapped <- renewed })
	}()

	// Several ambiguous retries pass; the pending id never changes and is
	// never deleted while the server stays unreachable.
	time.Sleep(150 * time.Millisecond)
	_, renewalIDDuring, stillOK := identity.PendingRenewal(dir)
	if !stillOK || renewalIDDuring != renewalIDBefore {
		t.Fatalf("pending renewal id must be preserved across retries: %v %v",
			renewalIDDuring, stillOK)
	}

	// Server becomes reachable: the SAME pending activates and promotes.
	reachable.Store("down", false)
	select {
	case reconciled := <-swapped:
		if !server.accepts(reconciled) {
			t.Fatal("reconciled key must be accepted")
		}
	case <-time.After(10 * time.Second):
		t.Fatal("reconciliation never completed after the server returned")
	}
	cancel()
	select {
	case <-loopDone:
	case <-time.After(5 * time.Second):
		t.Fatal("RenewalLoop leaked after cancellation")
	}
}

type senderFunc func(ctx context.Context, path string, payload any) (int, []byte, error)

func (f senderFunc) Post(ctx context.Context, path string, payload any) (int, []byte, error) {
	return f(ctx, path, payload)
}

func TestCancellationDuringAmbiguousRetriesIsClean(t *testing.T) {
	shrinkBackoff(t)
	dir := t.TempDir()
	current := seededIdentity(t, dir)
	server := &statefulServer{t: t, acceptedKey: &current.Key().PublicKey}
	failing := func(id *identity.Identity) (Sender, error) {
		inner, _ := server.factory()(id)
		return senderFunc(func(ctx context.Context, path string, payload any) (int, []byte, error) {
			if strings.HasSuffix(path, "/activate") {
				return 0, nil, errors.New("transport: down")
			}
			return inner.Post(ctx, path, payload)
		}), nil
	}
	if _, err := renewOnce(context.Background(), failing, current, dir); err == nil {
		t.Fatal("expected ambiguous failure")
	}

	ctx, cancel := context.WithCancel(context.Background())
	loopDone := make(chan struct{})
	go func() {
		defer close(loopDone)
		RenewalLoop(ctx, failing, current, dir, testLogger(), func(*identity.Identity) {})
	}()
	time.Sleep(60 * time.Millisecond) // land inside the retry/backoff loop
	cancel()
	select {
	case <-loopDone:
	case <-time.After(5 * time.Second):
		t.Fatal("RenewalLoop leaked goroutines/timers after cancellation")
	}
	// Pending must still be intact for the next process/loop.
	if _, _, ok := identity.PendingRenewal(dir); !ok {
		t.Fatal("cancellation must not cost the pending material")
	}
}

func TestExplicitRefusalDiscardsPendingWithoutAssumingNewKey(t *testing.T) {
	shrinkBackoff(t)
	dir := t.TempDir()
	current := seededIdentity(t, dir)
	server := &statefulServer{
		t: t, acceptedKey: &current.Key().PublicKey, refuseActivations: true,
	}

	_, err := renewOnce(context.Background(), server.factory(), current, dir)
	if err == nil {
		t.Fatal("refused activation must surface as an error")
	}
	if _, _, pending := identity.PendingRenewal(dir); pending {
		t.Fatal("an explicit refusal must discard the pending marker")
	}
	still, loadErr := identity.LoadCurrent(dir)
	if loadErr != nil || !still.Key().Equal(current.Key()) {
		t.Fatalf("the refused new key must never be assumed current: %v", loadErr)
	}
	if !server.accepts(current) {
		t.Fatal("old key must keep working after an explicit refusal")
	}
}

func TestPrepareFailureLeavesNoPendingState(t *testing.T) {
	dir := t.TempDir()
	current := seededIdentity(t, dir)
	server := &statefulServer{t: t, acceptedKey: &current.Key().PublicKey, refusePrepares: true}
	_, err := renewOnce(context.Background(), server.factory(), current, dir)
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

func TestStartupReconcileWithRefusedPendingFallsBackToCurrent(t *testing.T) {
	dir := t.TempDir()
	current := seededIdentity(t, dir)
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
	server := &statefulServer{
		t: t, acceptedKey: &current.Key().PublicKey, refuseActivations: true,
	}
	reconciled := ReconcilePendingRenewal(
		context.Background(), server.factory(), dir, current, testLogger(),
	)
	if !reconciled.Key().Equal(current.Key()) {
		t.Fatal("refused pending must fall back to the current identity")
	}
	if _, _, pending := identity.PendingRenewal(dir); pending {
		t.Fatal("refused pending must be discarded")
	}
}

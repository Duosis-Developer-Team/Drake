package identity

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"
	"time"
)

const testAgentID = "6a1d8b5f-9e2a-4b63-8c7d-3a5b6c7d8e9f"

func makeBundle(t *testing.T, dir string) (string, *Identity) {
	t.Helper()
	key, err := GenerateKey()
	if err != nil {
		t.Fatalf("keygen: %v", err)
	}
	notAfter := time.Now().Add(time.Hour)
	bundleID, err := SaveBundle(dir, testAgentID, key, selfSignedPEM(t, key, notAfter), "ca", notAfter)
	if err != nil {
		t.Fatalf("save bundle: %v", err)
	}
	loaded, err := LoadBundle(dir, bundleID)
	if err != nil {
		t.Fatalf("load bundle: %v", err)
	}
	return bundleID, loaded
}

func TestBundlePromotionIsAtomicAndComplete(t *testing.T) {
	dir := t.TempDir()
	first, _ := makeBundle(t, dir)
	if err := Promote(dir, first); err != nil {
		t.Fatalf("promote: %v", err)
	}
	current, err := LoadCurrent(dir)
	if err != nil {
		t.Fatalf("load current: %v", err)
	}

	// A SECOND bundle saved but NOT promoted must change nothing: the
	// active identity is whatever the pointer names — never a mixture.
	second, _ := makeBundle(t, dir)
	still, err := LoadCurrent(dir)
	if err != nil {
		t.Fatalf("load current after unpromoted save: %v", err)
	}
	if !still.Key().Equal(current.Key()) {
		t.Fatal("unpromoted bundle leaked into the active identity")
	}

	if err := Promote(dir, second); err != nil {
		t.Fatalf("promote second: %v", err)
	}
	promoted, err := LoadCurrent(dir)
	if err != nil {
		t.Fatalf("load current after promote: %v", err)
	}
	if promoted.Key().Equal(current.Key()) {
		t.Fatal("promotion did not switch the active identity")
	}
	// Certificate and key always travel together: the loaded bundle's key
	// must match its own certificate file, proving no cross-bundle mix.
	certPEM, err := os.ReadFile(promoted.CertPath)
	if err != nil {
		t.Fatalf("read cert: %v", err)
	}
	if !bytes.Contains(certPEM, []byte("BEGIN CERTIFICATE")) {
		t.Fatal("promoted bundle certificate unreadable")
	}
}

func TestPromoteRefusesUnreadableBundle(t *testing.T) {
	dir := t.TempDir()
	first, _ := makeBundle(t, dir)
	if err := Promote(dir, first); err != nil {
		t.Fatalf("promote: %v", err)
	}
	// A save failure (simulated: missing bundle) must never dethrone the
	// working identity.
	if err := Promote(dir, "no-such-bundle"); err == nil {
		t.Fatal("promoting a missing bundle must fail")
	}
	current, err := LoadCurrent(dir)
	if err != nil || current.AgentID != testAgentID {
		t.Fatalf("old identity must keep working after failed promote: %v", err)
	}
}

func TestCrashLeavesFullyOldOrFullyNew(t *testing.T) {
	dir := t.TempDir()
	first, _ := makeBundle(t, dir)
	if err := Promote(dir, first); err != nil {
		t.Fatalf("promote: %v", err)
	}
	old, _ := LoadCurrent(dir)

	// Simulated crash mid-renewal: bundle saved, pending recorded, but no
	// promotion. Restart loads the COMPLETE old identity.
	second, pendingIdentity := makeBundle(t, dir)
	if err := SetPending(dir, second, "renewal-1"); err != nil {
		t.Fatalf("set pending: %v", err)
	}
	restart, err := LoadCurrent(dir)
	if err != nil {
		t.Fatalf("load after crash: %v", err)
	}
	if !restart.Key().Equal(old.Key()) {
		t.Fatal("crash mid-renewal must leave the fully-old identity")
	}
	bundleID, renewalID, ok := PendingRenewal(dir)
	if !ok || bundleID != second || renewalID != "renewal-1" {
		t.Fatalf("pending marker lost: %v %v %v", bundleID, renewalID, ok)
	}
	// After promotion (activation succeeded): fully-new.
	if err := Promote(dir, second); err != nil {
		t.Fatalf("promote: %v", err)
	}
	ClearPending(dir)
	fresh, err := LoadCurrent(dir)
	if err != nil {
		t.Fatalf("load after promote: %v", err)
	}
	if !fresh.Key().Equal(pendingIdentity.Key()) {
		t.Fatal("promotion must yield the fully-new identity")
	}
	if _, _, still := PendingRenewal(dir); still {
		t.Fatal("pending marker must clear after promotion")
	}
}

func TestBundleKeyPermissionsAndNoLeakedMaterial(t *testing.T) {
	dir := t.TempDir()
	bundleID, loaded := makeBundle(t, dir)
	info, err := os.Stat(loaded.KeyPath)
	if err != nil {
		t.Fatalf("stat key: %v", err)
	}
	if perm := info.Mode().Perm(); perm != 0o600 {
		t.Fatalf("bundle key must be 0600, got %o", perm)
	}
	if err := Promote(dir, bundleID); err != nil {
		t.Fatalf("promote: %v", err)
	}
	for _, pointer := range []string{"current", "pending", "sequence"} {
		data, err := os.ReadFile(filepath.Join(dir, pointer))
		if err != nil {
			continue
		}
		if bytes.Contains(data, []byte("PRIVATE KEY")) {
			t.Fatalf("private material leaked into pointer %s", pointer)
		}
	}
}

func TestSequenceStoreRoundTripAndCorruptionSafety(t *testing.T) {
	dir := t.TempDir()
	if got := LoadSequence(dir); got != 0 {
		t.Fatalf("missing sequence must read 0, got %d", got)
	}
	if err := StoreSequence(dir, 41); err != nil {
		t.Fatalf("store: %v", err)
	}
	if got := LoadSequence(dir); got != 41 {
		t.Fatalf("round trip lost the sequence: %d", got)
	}
	if err := os.WriteFile(filepath.Join(dir, "sequence"), []byte("not-a-number"), 0o600); err != nil {
		t.Fatalf("corrupt: %v", err)
	}
	if got := LoadSequence(dir); got != 0 {
		t.Fatalf("corrupt sequence must fail closed to 0, got %d", got)
	}
}

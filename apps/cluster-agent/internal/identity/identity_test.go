package identity

import (
	"bytes"
	"crypto/ecdsa"
	"crypto/rand"
	"crypto/sha256"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/hex"
	"encoding/pem"
	"fmt"
	"math/big"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// selfSignedPEM builds a throwaway certificate for round-trip tests only.
func selfSignedPEM(t *testing.T, key *ecdsa.PrivateKey, notAfter time.Time) string {
	t.Helper()
	template := &x509.Certificate{
		SerialNumber: big.NewInt(1),
		Subject:      pkix.Name{CommonName: "drake-agent-test"},
		NotBefore:    notAfter.Add(-time.Hour),
		NotAfter:     notAfter,
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		t.Fatalf("self-sign: %v", err)
	}
	return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}))
}

func TestSaveLoadRoundTripAndPermissions(t *testing.T) {
	dir := t.TempDir()
	key, err := GenerateKey()
	if err != nil {
		t.Fatalf("keygen: %v", err)
	}
	notAfter := time.Now().Add(14 * 24 * time.Hour).Truncate(time.Second)
	saved, err := Save(dir, "6a1d8b5f-9e2a-4b63-8c7d-3a5b6c7d8e9f", key,
		selfSignedPEM(t, key, notAfter), "ca-material", notAfter)
	if err != nil {
		t.Fatalf("save: %v", err)
	}

	info, err := os.Stat(saved.KeyPath)
	if err != nil {
		t.Fatalf("stat key: %v", err)
	}
	if perm := info.Mode().Perm(); perm != 0o600 {
		t.Fatalf("private key must be 0600, got %o", perm)
	}

	loaded, err := Load(dir)
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	if loaded.AgentID != saved.AgentID {
		t.Fatalf("agent id lost: %q", loaded.AgentID)
	}
	if !loaded.Key().Equal(key) {
		t.Fatal("loaded key differs from saved key")
	}
	if !loaded.NotAfter.Equal(notAfter) {
		t.Fatalf("expiry lost: %v vs %v", loaded.NotAfter, notAfter)
	}
}

func TestPopHeadersSignatureVerifies(t *testing.T) {
	dir := t.TempDir()
	key, err := GenerateKey()
	if err != nil {
		t.Fatalf("keygen: %v", err)
	}
	notAfter := time.Now().Add(time.Hour)
	id, err := Save(dir, "6a1d8b5f-9e2a-4b63-8c7d-3a5b6c7d8e9f", key,
		selfSignedPEM(t, key, notAfter), "ca", notAfter)
	if err != nil {
		t.Fatalf("save: %v", err)
	}

	body := []byte(`{"kind":"heartbeat"}`)
	headers, err := id.PopHeaders("POST", "/internal/v1/agent/heartbeat", body)
	if err != nil {
		t.Fatalf("pop headers: %v", err)
	}
	for _, name := range []string{
		"X-Drake-Agent-Id", "X-Drake-Agent-Timestamp",
		"X-Drake-Agent-Nonce", "X-Drake-Agent-Signature",
	} {
		if headers[name] == "" {
			t.Fatalf("missing header %q", name)
		}
	}

	// Reconstruct the exact canonical string the server verifies.
	bodyHash := sha256.Sum256(body)
	message := fmt.Sprintf("POST\n/internal/v1/agent/heartbeat\n%s\n%s\n%s",
		hex.EncodeToString(bodyHash[:]),
		headers["X-Drake-Agent-Timestamp"],
		headers["X-Drake-Agent-Nonce"],
	)
	digest := sha256.Sum256([]byte(message))
	signature, err := base64.StdEncoding.DecodeString(headers["X-Drake-Agent-Signature"])
	if err != nil {
		t.Fatalf("signature not base64: %v", err)
	}
	if !ecdsa.VerifyASN1(&key.PublicKey, digest[:], signature) {
		t.Fatal("signature does not verify against the canonical string")
	}

	// A tampered body must not verify.
	tamperedHash := sha256.Sum256([]byte(`{"kind":"heartbeat","extra":true}`))
	tampered := fmt.Sprintf("POST\n/internal/v1/agent/heartbeat\n%s\n%s\n%s",
		hex.EncodeToString(tamperedHash[:]),
		headers["X-Drake-Agent-Timestamp"],
		headers["X-Drake-Agent-Nonce"],
	)
	tamperedDigest := sha256.Sum256([]byte(tampered))
	if ecdsa.VerifyASN1(&key.PublicKey, tamperedDigest[:], signature) {
		t.Fatal("signature verified a tampered body")
	}
}

func TestPrivateKeyNeverInCertOrCAFiles(t *testing.T) {
	dir := t.TempDir()
	key, err := GenerateKey()
	if err != nil {
		t.Fatalf("keygen: %v", err)
	}
	notAfter := time.Now().Add(time.Hour)
	id, err := Save(dir, "6a1d8b5f-9e2a-4b63-8c7d-3a5b6c7d8e9f", key,
		selfSignedPEM(t, key, notAfter), "ca-material", notAfter)
	if err != nil {
		t.Fatalf("save: %v", err)
	}
	for _, path := range []string{id.CertPath, id.CAPath, filepath.Join(dir, "agent-id")} {
		data, err := os.ReadFile(path)
		if err != nil {
			t.Fatalf("read %s: %v", path, err)
		}
		if bytes.Contains(data, []byte("PRIVATE KEY")) {
			t.Fatalf("private material leaked into %s", path)
		}
	}
}

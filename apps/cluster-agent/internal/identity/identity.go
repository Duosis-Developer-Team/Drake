// Package identity manages the agent's cryptographic identity.
//
// The private key is generated locally, written atomically with 0600
// permissions, and NEVER leaves this process — enrollment and renewal send
// only CSRs. Requests to the Drake API carry a proof-of-possession
// signature (ECDSA P-256 over method\npath\nsha256(body)\ntimestamp\nnonce)
// so identity can never be spoofed by headers alone (ADR-0016).
package identity

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/hex"
	"encoding/pem"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/google/uuid"
)

// Identity is the agent's enrolled identity material on disk.
type Identity struct {
	AgentID  string
	KeyPath  string
	CertPath string
	CAPath   string
	key      *ecdsa.PrivateKey
	NotAfter time.Time
}

// GenerateKey creates a new P-256 keypair (in memory only).
func GenerateKey() (*ecdsa.PrivateKey, error) {
	return ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
}

// CSRPEM builds a PEM-encoded certificate signing request for the key.
func CSRPEM(key *ecdsa.PrivateKey) (string, error) {
	der, err := x509.CreateCertificateRequest(
		rand.Reader,
		&x509.CertificateRequest{Subject: pkix.Name{CommonName: "drake-agent"}},
		key,
	)
	if err != nil {
		return "", fmt.Errorf("create csr: %w", err)
	}
	return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE REQUEST", Bytes: der})), nil
}

// atomicWrite writes data to path via a temp file + rename, mode 0600.
func atomicWrite(path string, data []byte) error {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, ".tmp-*")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if err := tmp.Chmod(0o600); err != nil {
		tmp.Close()
		return err
	}
	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmp.Name(), path)
}

// Save persists the identity material (key/cert/CA chain) atomically.
func Save(dir, agentID string, key *ecdsa.PrivateKey, certPEM, caPEM string, notAfter time.Time) (*Identity, error) {
	keyDER, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		return nil, fmt.Errorf("marshal key: %w", err)
	}
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: keyDER})
	id := &Identity{
		AgentID:  agentID,
		KeyPath:  filepath.Join(dir, "agent-key.pem"),
		CertPath: filepath.Join(dir, "agent.pem"),
		CAPath:   filepath.Join(dir, "ca.pem"),
		key:      key,
		NotAfter: notAfter,
	}
	if err := atomicWrite(id.KeyPath, keyPEM); err != nil {
		return nil, err
	}
	if err := atomicWrite(id.CertPath, []byte(certPEM)); err != nil {
		return nil, err
	}
	if err := atomicWrite(filepath.Join(dir, "agent-id"), []byte(agentID)); err != nil {
		return nil, err
	}
	if err := atomicWrite(id.CAPath, []byte(caPEM)); err != nil {
		return nil, err
	}
	return id, nil
}

// Load restores a previously saved identity, if present and parseable.
func Load(dir string) (*Identity, error) {
	keyPEM, err := os.ReadFile(filepath.Join(dir, "agent-key.pem"))
	if err != nil {
		return nil, err
	}
	block, _ := pem.Decode(keyPEM)
	if block == nil {
		return nil, fmt.Errorf("no key material")
	}
	parsed, err := x509.ParsePKCS8PrivateKey(block.Bytes)
	if err != nil {
		return nil, fmt.Errorf("parse key: %w", err)
	}
	key, ok := parsed.(*ecdsa.PrivateKey)
	if !ok {
		return nil, fmt.Errorf("unexpected key type")
	}
	agentID, err := os.ReadFile(filepath.Join(dir, "agent-id"))
	if err != nil {
		return nil, err
	}
	certPEM, err := os.ReadFile(filepath.Join(dir, "agent.pem"))
	if err != nil {
		return nil, err
	}
	certBlock, _ := pem.Decode(certPEM)
	if certBlock == nil {
		return nil, fmt.Errorf("no certificate material")
	}
	cert, err := x509.ParseCertificate(certBlock.Bytes)
	if err != nil {
		return nil, err
	}
	return &Identity{
		AgentID:  string(agentID),
		KeyPath:  filepath.Join(dir, "agent-key.pem"),
		CertPath: filepath.Join(dir, "agent.pem"),
		CAPath:   filepath.Join(dir, "ca.pem"),
		key:      key,
		NotAfter: cert.NotAfter,
	}, nil
}

// Key exposes the private key to the transport (mTLS) — never serialized.
func (i *Identity) Key() *ecdsa.PrivateKey { return i.key }

// PopHeaders signs a request with the enrolled key (proof of possession).
func (i *Identity) PopHeaders(method, path string, body []byte) (map[string]string, error) {
	timestamp := fmt.Sprintf("%d", time.Now().Unix())
	nonce := uuid.NewString()
	bodyHash := sha256.Sum256(body)
	message := fmt.Sprintf("%s\n%s\n%s\n%s\n%s", method, path, hex.EncodeToString(bodyHash[:]), timestamp, nonce)
	digest := sha256.Sum256([]byte(message))
	signature, err := ecdsa.SignASN1(rand.Reader, i.key, digest[:])
	if err != nil {
		return nil, fmt.Errorf("sign request: %w", err)
	}
	return map[string]string{
		"X-Drake-Agent-Id":        i.AgentID,
		"X-Drake-Agent-Timestamp": timestamp,
		"X-Drake-Agent-Nonce":     nonce,
		"X-Drake-Agent-Signature": base64.StdEncoding.EncodeToString(signature),
	}, nil
}

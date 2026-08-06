package identity

// Versioned, crash-safe identity storage.
//
// Layout under the state directory:
//
//	bundles/<bundle-id>/   agent-key.pem agent.pem ca.pem agent-id
//	current                file naming the ACTIVE bundle id
//	pending                file naming a prepared-but-unactivated bundle
//	                       ("<bundle-id> <renewal-id>")
//	sequence               last ACKed inventory sequence
//
// A bundle directory is inert until a pointer names it, and pointers are
// replaced via atomic rename — so a crash at any instant leaves either the
// complete old identity or the complete new one, never a mixture (key from
// one generation with the certificate of another is impossible).

import (
	"crypto/ecdsa"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
)

const (
	currentPointer  = "current"
	pendingPointer  = "pending"
	sequenceFile    = "sequence"
	bundlesDirName  = "bundles"
	maxKeptBundles  = 4
	pointerFileMode = 0o600
)

// SaveBundle writes a complete identity bundle WITHOUT activating it and
// returns its id. Nothing references the bundle until a pointer does.
func SaveBundle(
	dir, agentID string, key *ecdsa.PrivateKey, certPEM, caPEM string, notAfter time.Time,
) (string, error) {
	bundleID := uuid.NewString()
	bundleDir := filepath.Join(dir, bundlesDirName, bundleID)
	if _, err := Save(bundleDir, agentID, key, certPEM, caPEM, notAfter); err != nil {
		return "", err
	}
	return bundleID, nil
}

// Promote atomically makes a bundle the ACTIVE identity.
func Promote(dir, bundleID string) error {
	if _, err := LoadBundle(dir, bundleID); err != nil {
		return fmt.Errorf("refusing to promote unreadable bundle: %w", err)
	}
	if err := atomicWrite(filepath.Join(dir, currentPointer), []byte(bundleID)); err != nil {
		return err
	}
	pruneBundles(dir)
	return nil
}

// LoadBundle loads one specific bundle by id.
func LoadBundle(dir, bundleID string) (*Identity, error) {
	return Load(filepath.Join(dir, bundlesDirName, bundleID))
}

// LoadCurrent loads the active identity via the current pointer, falling
// back to the legacy flat layout for pre-bundle state directories.
func LoadCurrent(dir string) (*Identity, error) {
	pointer, err := os.ReadFile(filepath.Join(dir, currentPointer))
	if err == nil {
		return LoadBundle(dir, strings.TrimSpace(string(pointer)))
	}
	return Load(dir)
}

// SetPending records a prepared renewal (bundle + renewal id) atomically.
func SetPending(dir, bundleID, renewalID string) error {
	return atomicWrite(
		filepath.Join(dir, pendingPointer), []byte(bundleID+" "+renewalID),
	)
}

// PendingRenewal returns (bundleID, renewalID, true) when a prepared
// renewal awaits activation.
func PendingRenewal(dir string) (string, string, bool) {
	raw, err := os.ReadFile(filepath.Join(dir, pendingPointer))
	if err != nil {
		return "", "", false
	}
	parts := strings.Fields(string(raw))
	if len(parts) != 2 {
		return "", "", false
	}
	return parts[0], parts[1], true
}

// ClearPending forgets a pending renewal (after promotion or refusal).
func ClearPending(dir string) {
	_ = os.Remove(filepath.Join(dir, pendingPointer))
}

// LoadSequence returns the last ACKed inventory sequence (0 when absent).
func LoadSequence(dir string) int64 {
	raw, err := os.ReadFile(filepath.Join(dir, sequenceFile))
	if err != nil {
		return 0
	}
	value, err := strconv.ParseInt(strings.TrimSpace(string(raw)), 10, 64)
	if err != nil || value < 0 {
		return 0
	}
	return value
}

// StoreSequence persists the last ACKed inventory sequence atomically.
// The engine only advances past a sequence AFTER the server acknowledged
// it, so a crash between ACK and persist replays an idempotent message.
func StoreSequence(dir string, value int64) error {
	return atomicWrite(
		filepath.Join(dir, sequenceFile), []byte(strconv.FormatInt(value, 10)),
	)
}

// pruneBundles removes old bundle directories, keeping the referenced and
// most recent few. Best-effort: pruning failures never break identity.
func pruneBundles(dir string) {
	referenced := map[string]bool{}
	if raw, err := os.ReadFile(filepath.Join(dir, currentPointer)); err == nil {
		referenced[strings.TrimSpace(string(raw))] = true
	}
	if bundleID, _, ok := PendingRenewal(dir); ok {
		referenced[bundleID] = true
	}
	entries, err := os.ReadDir(filepath.Join(dir, bundlesDirName))
	if err != nil || len(entries) <= maxKeptBundles {
		return
	}
	type aged struct {
		name string
		mod  time.Time
	}
	var candidates []aged
	for _, entry := range entries {
		if !entry.IsDir() || referenced[entry.Name()] {
			continue
		}
		info, err := entry.Info()
		if err != nil {
			continue
		}
		candidates = append(candidates, aged{entry.Name(), info.ModTime()})
	}
	excess := len(entries) - maxKeptBundles
	for index := 0; index < len(candidates) && excess > 0; index++ {
		oldest := 0
		for candidate := range candidates {
			if candidates[candidate].mod.Before(candidates[oldest].mod) {
				oldest = candidate
			}
		}
		_ = os.RemoveAll(filepath.Join(dir, bundlesDirName, candidates[oldest].name))
		candidates[oldest].mod = time.Now().Add(24 * time.Hour) // consumed
		excess--
	}
}

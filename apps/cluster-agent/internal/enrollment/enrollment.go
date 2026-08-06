// Package enrollment exchanges a one-time token for the agent's identity.
//
// The private key is generated locally and never leaves the process; only
// the CSR travels. Tokens and keys never appear in logs or errors.
package enrollment

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"time"

	"github.com/Duosis-Developer-Team/Drake/apps/cluster-agent/internal/identity"
)

// ErrRefused is returned for any enrollment rejection (no detail leaks).
var ErrRefused = errors.New("enrollment refused")

type response struct {
	AgentID             string `json:"agent_id"`
	ClusterID           string `json:"cluster_id"`
	CertificatePEM      string `json:"certificate_pem"`
	CAChainPEM          string `json:"ca_chain_pem"`
	CertificateNotAfter string `json:"certificate_not_after"`
}

// Exchange enrolls against the internal API and persists the identity.
func Exchange(ctx context.Context, endpoint, serverCAFile, token, clusterID, agentVersion, stateDir string) (*identity.Identity, error) {
	key, err := identity.GenerateKey()
	if err != nil {
		return nil, fmt.Errorf("generate key: %w", err)
	}
	csr, err := identity.CSRPEM(key)
	if err != nil {
		return nil, err
	}
	payload, err := json.Marshal(map[string]any{
		"api_version":   "drake.duosis.com/agent/v1",
		"kind":          "enrollment_request",
		"token":         token,
		"csr_pem":       csr,
		"cluster_id":    clusterID,
		"agent_version": agentVersion,
	})
	if err != nil {
		return nil, err
	}

	caPool := x509.NewCertPool()
	caPEM, err := os.ReadFile(serverCAFile)
	if err != nil {
		return nil, fmt.Errorf("read server ca: %w", err)
	}
	if !caPool.AppendCertsFromPEM(caPEM) {
		return nil, fmt.Errorf("server ca not parseable")
	}
	client := &http.Client{
		Timeout: 15 * time.Second,
		Transport: &http.Transport{
			TLSClientConfig: &tls.Config{RootCAs: caPool, MinVersion: tls.VersionTLS12},
		},
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
	request, err := http.NewRequestWithContext(
		ctx, http.MethodPost, endpoint+"/internal/v1/agent/enroll", bytes.NewReader(payload),
	)
	if err != nil {
		return nil, err
	}
	request.Header.Set("Content-Type", "application/json")
	resp, err := client.Do(request)
	if err != nil {
		return nil, fmt.Errorf("enrollment transport: %w", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode != http.StatusCreated {
		return nil, ErrRefused
	}
	var enrolled response
	if err := json.Unmarshal(body, &enrolled); err != nil {
		return nil, fmt.Errorf("enrollment response malformed: %w", err)
	}
	notAfter, err := time.Parse(time.RFC3339, enrolled.CertificateNotAfter)
	if err != nil {
		notAfter = time.Now().Add(24 * time.Hour)
	}
	return identity.Save(stateDir, enrolled.AgentID, key, enrolled.CertificatePEM, enrolled.CAChainPEM, notAfter)
}

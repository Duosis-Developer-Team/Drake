// Package relay runs Tracesis's reads against the observability stores
// inside this cluster (ADR-0037 §3).
//
// The agent pulls: each worker holds a long poll on the ingest listener
// (`/internal/v1/agent/relay/next`), runs what it is handed, and posts the
// answer (`/relay/result`). Nothing dials the agent, and nothing it is
// handed can reach the Kubernetes API, a Secret or a write verb — the only
// targets are the two store URLs in its configuration.
//
// What it will run is closed:
//
//	clickhouse.query        POST <clickhouse>/  statement as the body, typed
//	                        param_* values and four narrowing settings only
//	prometheus.query_range  POST <prometheus>/api/v1/query_range
//	                        query, start, end, step only
//
// Anything else is answered with a refusal code, never executed. Response
// bodies are capped, redirects are never followed, and an error body from a
// store (which may quote the statement) is never forwarded.
package relay

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"math/rand"
	"net"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strings"
	"sync"
	"time"
)

const (
	// NextPath and ResultPath are the listener routes.
	NextPath   = "/internal/v1/agent/relay/next"
	ResultPath = "/internal/v1/agent/relay/result"

	// pollWaitSeconds is how long the listener may hold a poll open; the
	// HTTP call waits a little longer than that.
	pollWaitSeconds = 20
	pollTimeout     = 30 * time.Second

	// The same caps the API applies to a direct read.
	maxClickHouseBytes = 4 << 20
	maxPrometheusBytes = 2 << 20

	// No read runs longer than this, whatever deadline it carries.
	maxReadDuration = 30 * time.Second
)

// Sender is the agent's authenticated transport.
type Sender interface {
	Post(ctx context.Context, path string, payload any) (int, []byte, error)
	PostOnce(ctx context.Context, path string, payload any, timeout time.Duration) (int, []byte, error)
}

// Config names the stores this agent may read. An empty URL means that kind
// of read is answered `not_configured`.
type Config struct {
	ClickHouseURL          string
	ClickHouseUser         string
	ClickHousePasswordFile string
	PrometheusURL          string
	Workers                int
}

// Enabled reports whether there is any store to relay to.
func (c Config) Enabled() bool { return c.ClickHouseURL != "" || c.PrometheusURL != "" }

// Validate refuses a store URL that is not plain http(s) to a host, or that
// carries credentials.
func (c Config) Validate() error {
	for name, raw := range map[string]string{"clickhouse": c.ClickHouseURL, "prometheus": c.PrometheusURL} {
		if raw == "" {
			continue
		}
		parsed, err := url.Parse(raw)
		if err != nil || parsed.Host == "" {
			return fmt.Errorf("relay %s url is not a valid URL", name)
		}
		if parsed.Scheme != "http" && parsed.Scheme != "https" {
			return fmt.Errorf("relay %s url must be http or https", name)
		}
		if parsed.User != nil {
			return fmt.Errorf("relay %s url must not embed credentials", name)
		}
		if parsed.RawQuery != "" || parsed.Fragment != "" {
			return fmt.Errorf("relay %s url must not carry a query", name)
		}
	}
	if c.Enabled() && (c.Workers < 1 || c.Workers > 16) {
		return fmt.Errorf("relay workers must be within [1, 16]")
	}
	return nil
}

// Item is one read handed out by the listener.
type Item struct {
	ID       string          `json:"id"`
	Kind     string          `json:"kind"`
	Deadline float64         `json:"deadline"`
	Request  json.RawMessage `json:"request"`
}

// Result is what goes back. Exactly one of StatusCode or Error is set.
type Result struct {
	ID          string `json:"id"`
	StatusCode  int    `json:"status_code,omitempty"`
	ContentType string `json:"content_type,omitempty"`
	BodyB64     string `json:"body_b64,omitempty"`
	Error       string `json:"error,omitempty"`
}

// Relay is the worker pool.
type Relay struct {
	cfg    Config
	sender Sender
	http   *http.Client
	logger *slog.Logger
}

// New builds a relay; the HTTP client never follows redirects.
func New(cfg Config, sender Sender, logger *slog.Logger) *Relay {
	return &Relay{
		cfg:    cfg,
		sender: sender,
		logger: logger,
		http: &http.Client{
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
		},
	}
}

// Run polls until ctx ends.
func (r *Relay) Run(ctx context.Context) {
	var group sync.WaitGroup
	for worker := 0; worker < r.cfg.Workers; worker++ {
		group.Add(1)
		go func() {
			defer group.Done()
			r.loop(ctx)
		}()
	}
	group.Wait()
}

func (r *Relay) loop(ctx context.Context) {
	failures := 0
	for ctx.Err() == nil {
		status, body, err := r.sender.PostOnce(ctx, NextPath, map[string]int{"wait_seconds": pollWaitSeconds}, pollTimeout)
		switch {
		case err != nil || status >= 500 || (status != http.StatusOK && status != http.StatusNoContent):
			failures++
			if err != nil && ctx.Err() == nil {
				r.logger.Warn("relay poll failed", "error", err.Error())
			} else if err == nil {
				r.logger.Warn("relay poll refused", "status", status)
			}
			sleep(ctx, backoff(failures))
			continue
		case status == http.StatusNoContent:
			failures = 0
			continue
		}
		failures = 0
		var item Item
		if err := json.Unmarshal(body, &item); err != nil || item.ID == "" {
			r.logger.Warn("relay item unreadable")
			continue
		}
		result := r.Execute(ctx, item)
		answerStatus, _, err := r.sender.Post(ctx, ResultPath, result)
		if err != nil && ctx.Err() == nil {
			r.logger.Warn("relay answer failed", "error", err.Error())
		} else if answerStatus == http.StatusNotFound {
			// The caller gave up first; nothing to do.
			r.logger.Debug("relay answer arrived after its caller left")
		}
	}
}

// Execute runs one item and never returns an error: every failure is a
// bounded code in the result.
func (r *Relay) Execute(ctx context.Context, item Item) Result {
	deadline := time.Now().Add(maxReadDuration)
	if item.Deadline > 0 {
		if at := time.Unix(0, int64(item.Deadline*float64(time.Second))); at.Before(deadline) {
			deadline = at
		}
	}
	if !time.Now().Before(deadline) {
		return Result{ID: item.ID, Error: "timeout"}
	}
	readCtx, cancel := context.WithDeadline(ctx, deadline)
	defer cancel()

	started := time.Now()
	var result Result
	switch item.Kind {
	case "clickhouse.query":
		result = r.clickhouse(readCtx, item.Request)
	case "prometheus.query_range":
		result = r.prometheus(readCtx, item.Request)
	default:
		result = Result{Error: "kind_refused"}
	}
	result.ID = item.ID
	r.logger.Debug("relay read", "kind", item.Kind, "status", result.StatusCode,
		"error", result.Error, "ms", time.Since(started).Milliseconds())
	return result
}

// The settings a relayed ClickHouse read may carry. They narrow what the
// reader profile allows; the profile refuses anything above its ceilings.
var allowedSettings = map[string]func(string) bool{
	"max_result_rows":      isDigits,
	"result_overflow_mode": func(v string) bool { return v == "throw" },
	"max_execution_time":   isDigits,
	"default_format":       func(v string) bool { return v == "JSON" },
}

var paramName = regexp.MustCompile(`^param_[A-Za-z_][A-Za-z0-9_]{0,62}$`)

type clickhouseRequest struct {
	Statement string            `json:"statement"`
	Params    map[string]string `json:"params"`
}

func (r *Relay) clickhouse(ctx context.Context, raw json.RawMessage) Result {
	if r.cfg.ClickHouseURL == "" {
		return Result{Error: "not_configured"}
	}
	var request clickhouseRequest
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&request); err != nil || strings.TrimSpace(request.Statement) == "" {
		return Result{Error: "request_refused"}
	}
	query := url.Values{}
	for key, value := range request.Params {
		if check, ok := allowedSettings[key]; ok {
			if !check(value) {
				return Result{Error: "request_refused"}
			}
		} else if !paramName.MatchString(key) {
			return Result{Error: "request_refused"}
		}
		query.Set(key, value)
	}
	// JSON is the only format the API parses; never left to a default.
	query.Set("default_format", "JSON")
	target := strings.TrimRight(r.cfg.ClickHouseURL, "/") + "/?" + query.Encode()
	httpRequest, err := http.NewRequestWithContext(ctx, http.MethodPost, target, strings.NewReader(request.Statement))
	if err != nil {
		return Result{Error: "request_refused"}
	}
	if r.cfg.ClickHouseUser != "" {
		password, readErr := readSecret(r.cfg.ClickHousePasswordFile)
		if readErr != nil {
			return Result{Error: "unreachable"}
		}
		httpRequest.Header.Set("X-ClickHouse-User", r.cfg.ClickHouseUser)
		httpRequest.Header.Set("X-ClickHouse-Key", password)
	}
	return r.do(httpRequest, maxClickHouseBytes)
}

func (r *Relay) prometheus(ctx context.Context, raw json.RawMessage) Result {
	if r.cfg.PrometheusURL == "" {
		return Result{Error: "not_configured"}
	}
	var form map[string]string
	if err := json.Unmarshal(raw, &form); err != nil || len(form) != 4 {
		return Result{Error: "request_refused"}
	}
	values := url.Values{}
	for _, key := range []string{"query", "start", "end", "step"} {
		value, ok := form[key]
		if !ok || value == "" {
			return Result{Error: "request_refused"}
		}
		values.Set(key, value)
	}
	target := strings.TrimRight(r.cfg.PrometheusURL, "/") + "/api/v1/query_range"
	httpRequest, err := http.NewRequestWithContext(ctx, http.MethodPost, target, strings.NewReader(values.Encode()))
	if err != nil {
		return Result{Error: "request_refused"}
	}
	httpRequest.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	return r.do(httpRequest, maxPrometheusBytes)
}

func (r *Relay) do(request *http.Request, limit int64) Result {
	response, err := r.http.Do(request)
	if err != nil {
		if isTimeout(err) {
			return Result{Error: "timeout"}
		}
		return Result{Error: "unreachable"}
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		// The status alone: an error body may quote the statement.
		return Result{StatusCode: response.StatusCode}
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, limit+1))
	if err != nil {
		if isTimeout(err) {
			return Result{Error: "timeout"}
		}
		return Result{Error: "unreachable"}
	}
	if int64(len(body)) > limit {
		return Result{Error: "too_large"}
	}
	return Result{
		StatusCode:  response.StatusCode,
		ContentType: response.Header.Get("Content-Type"),
		BodyB64:     base64.StdEncoding.EncodeToString(body),
	}
}

func readSecret(path string) (string, error) {
	if path == "" {
		return "", nil
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(raw)), nil
}

func isTimeout(err error) bool {
	if errors.Is(err, context.DeadlineExceeded) {
		return true
	}
	var netErr net.Error
	return errors.As(err, &netErr) && netErr.Timeout()
}

func isDigits(value string) bool {
	if value == "" || len(value) > 12 {
		return false
	}
	for _, char := range value {
		if char < '0' || char > '9' {
			return false
		}
	}
	return true
}

func backoff(failures int) time.Duration {
	if failures > 6 {
		failures = 6
	}
	base := time.Duration(1<<failures) * 500 * time.Millisecond
	return base + time.Duration(rand.Int63n(int64(base/2)+1)) //nolint:gosec // jitter only
}

func sleep(ctx context.Context, duration time.Duration) {
	select {
	case <-ctx.Done():
	case <-time.After(duration):
	}
}

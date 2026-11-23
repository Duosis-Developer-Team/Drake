package relay

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

func quietLogger() *slog.Logger { return slog.New(slog.NewTextHandler(io.Discard, nil)) }

func item(kind string, request any) Item {
	raw, _ := json.Marshal(request)
	return Item{ID: "0123456789abcdef0123456789abcdef", Kind: kind,
		Deadline: float64(time.Now().Add(10*time.Second).UnixNano()) / 1e9, Request: raw}
}

func TestClickHouseReadCarriesStatementParamsAndTheAgentsOwnCredential(t *testing.T) {
	var seen *http.Request
	var seenBody string
	store := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		seen = r
		raw, _ := io.ReadAll(r.Body)
		seenBody = string(raw)
		w.Header().Set("Content-Type", "application/json; charset=UTF-8")
		_, _ = w.Write([]byte(`{"data":[{"n":1}]}`))
	}))
	defer store.Close()
	secret := filepath.Join(t.TempDir(), "reader-password")
	if err := os.WriteFile(secret, []byte("s3cret\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	relay := New(Config{ClickHouseURL: store.URL, ClickHouseUser: "tracesis_reader",
		ClickHousePasswordFile: secret, Workers: 1}, nil, quietLogger())

	result := relay.Execute(context.Background(), item("clickhouse.query", map[string]any{
		"statement": "SELECT {cluster:String}",
		"params": map[string]string{"param_cluster": "kept-1", "max_result_rows": "10",
			"result_overflow_mode": "throw", "max_execution_time": "5", "default_format": "JSON"},
	}))

	if result.Error != "" || result.StatusCode != 200 {
		t.Fatalf("unexpected result %+v", result)
	}
	body, _ := base64.StdEncoding.DecodeString(result.BodyB64)
	if string(body) != `{"data":[{"n":1}]}` {
		t.Fatalf("body %q", body)
	}
	if seenBody != "SELECT {cluster:String}" {
		t.Fatalf("statement %q", seenBody)
	}
	query := seen.URL.Query()
	if query.Get("param_cluster") != "kept-1" || query.Get("default_format") != "JSON" {
		t.Fatalf("query %v", query)
	}
	if seen.Header.Get("X-ClickHouse-User") != "tracesis_reader" || seen.Header.Get("X-ClickHouse-Key") != "s3cret" {
		t.Fatal("the agent must authenticate with its own reader credential")
	}
}

func TestRefusedRequestsNeverReachTheStore(t *testing.T) {
	calls := 0
	store := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { calls++ }))
	defer store.Close()
	relay := New(Config{ClickHouseURL: store.URL, PrometheusURL: store.URL, Workers: 1}, nil, quietLogger())
	cases := []struct {
		name string
		item Item
		want string
	}{
		{"unknown kind", item("kubernetes.exec", map[string]any{}), "kind_refused"},
		{"a setting that widens", item("clickhouse.query", map[string]any{
			"statement": "SELECT 1", "params": map[string]string{"readonly": "0"}}), "request_refused"},
		{"another output format", item("clickhouse.query", map[string]any{
			"statement": "SELECT 1", "params": map[string]string{"default_format": "TSV"}}), "request_refused"},
		{"a non-numeric ceiling", item("clickhouse.query", map[string]any{
			"statement": "SELECT 1", "params": map[string]string{"max_result_rows": "1e9"}}), "request_refused"},
		{"an empty statement", item("clickhouse.query", map[string]any{"statement": "  "}), "request_refused"},
		{"an unknown field", item("clickhouse.query", map[string]any{
			"statement": "SELECT 1", "user": "default"}), "request_refused"},
		{"prometheus extra field", item("prometheus.query_range", map[string]string{
			"query": "up", "start": "1", "end": "2", "step": "60", "timeout": "1h"}), "request_refused"},
		{"prometheus missing field", item("prometheus.query_range", map[string]string{
			"query": "up", "start": "1", "end": "2"}), "request_refused"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := relay.Execute(context.Background(), tc.item); got.Error != tc.want {
				t.Fatalf("got %+v, want %s", got, tc.want)
			}
		})
	}
	if calls != 0 {
		t.Fatalf("a refused request reached the store %d times", calls)
	}
}

func TestPrometheusReadPostsTheFourFieldsOnly(t *testing.T) {
	var form map[string][]string
	var path string
	store := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path = r.URL.Path
		_ = r.ParseForm()
		form = r.PostForm
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"success"}`))
	}))
	defer store.Close()
	relay := New(Config{PrometheusURL: store.URL + "/", Workers: 1}, nil, quietLogger())
	result := relay.Execute(context.Background(), item("prometheus.query_range",
		map[string]string{"query": "up", "start": "1", "end": "2", "step": "60"}))
	if result.StatusCode != 200 || result.ContentType != "application/json" {
		t.Fatalf("result %+v", result)
	}
	if path != "/api/v1/query_range" || len(form) != 4 || form["query"][0] != "up" {
		t.Fatalf("path %s form %v", path, form)
	}
}

func TestStoreErrorBodiesAreNeverForwarded(t *testing.T) {
	store := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(400)
		_, _ = w.Write([]byte("Code: 62. Syntax error near SELECT secret_column"))
	}))
	defer store.Close()
	relay := New(Config{ClickHouseURL: store.URL, Workers: 1}, nil, quietLogger())
	result := relay.Execute(context.Background(), item("clickhouse.query", map[string]any{"statement": "SELECT 1"}))
	if result.StatusCode != 400 || result.BodyB64 != "" {
		t.Fatalf("result %+v", result)
	}
}

func TestOversizedAnswersAndRedirectsAndSilenceAreBoundedCodes(t *testing.T) {
	big := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(strings.Repeat("x", maxPrometheusBytes+10)))
	}))
	defer big.Close()
	redirect := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, "http://169.254.169.254/", http.StatusFound)
	}))
	defer redirect.Close()
	slow := httptest.NewServer(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
		select {
		case <-r.Context().Done():
		case <-time.After(5 * time.Second):
		}
	}))
	defer slow.Close()
	prom := func(url string) *Relay { return New(Config{PrometheusURL: url, Workers: 1}, nil, quietLogger()) }
	read := item("prometheus.query_range", map[string]string{"query": "up", "start": "1", "end": "2", "step": "60"})

	if got := prom(big.URL).Execute(context.Background(), read); got.Error != "too_large" {
		t.Fatalf("big: %+v", got)
	}
	if got := prom(redirect.URL).Execute(context.Background(), read); got.StatusCode != http.StatusFound || got.BodyB64 != "" {
		t.Fatalf("redirect must not be followed: %+v", got)
	}
	soon := read
	soon.Deadline = float64(time.Now().Add(200*time.Millisecond).UnixNano()) / 1e9
	if got := prom(slow.URL).Execute(context.Background(), soon); got.Error != "timeout" {
		t.Fatalf("slow: %+v", got)
	}
	past := read
	past.Deadline = float64(time.Now().Add(-time.Second).UnixNano()) / 1e9
	if got := prom(slow.URL).Execute(context.Background(), past); got.Error != "timeout" {
		t.Fatalf("expired: %+v", got)
	}
	unconfigured := New(Config{ClickHouseURL: big.URL, Workers: 1}, nil, quietLogger())
	if got := unconfigured.Execute(context.Background(), read); got.Error != "not_configured" {
		t.Fatalf("unconfigured: %+v", got)
	}
}

func TestConfigRefusesUnsafeStoreURLs(t *testing.T) {
	for _, raw := range []string{"ftp://x", "http://user:pw@x:8123", "not a url", "http://x/?user=default"} {
		if err := (Config{ClickHouseURL: raw, Workers: 1}).Validate(); err == nil {
			t.Fatalf("%q accepted", raw)
		}
	}
	if err := (Config{ClickHouseURL: "http://clickhouse.tracesis.svc:8123", Workers: 1}).Validate(); err != nil {
		t.Fatal(err)
	}
	if err := (Config{ClickHouseURL: "http://x:8123", Workers: 0}).Validate(); err == nil {
		t.Fatal("zero workers accepted")
	}
	if err := (Config{}).Validate(); err != nil {
		t.Fatalf("an agent without a relay must stay valid: %v", err)
	}
}

// fakeSender hands out queued items once, then 204s, and records answers.
type fakeSender struct {
	mu      sync.Mutex
	items   []Item
	answers []Result
	done    chan struct{}
}

func (f *fakeSender) PostOnce(ctx context.Context, path string, _ any, _ time.Duration) (int, []byte, error) {
	f.mu.Lock()
	if len(f.items) > 0 {
		next := f.items[0]
		f.items = f.items[1:]
		f.mu.Unlock()
		raw, _ := json.Marshal(next)
		return 200, raw, nil
	}
	f.mu.Unlock()
	select {
	case <-ctx.Done():
		return 0, nil, ctx.Err()
	case <-time.After(10 * time.Millisecond):
		return 204, nil, nil
	}
}

func (f *fakeSender) Post(_ context.Context, path string, payload any) (int, []byte, error) {
	if path != ResultPath {
		return 404, nil, nil
	}
	f.mu.Lock()
	f.answers = append(f.answers, payload.(Result))
	if len(f.answers) == 1 {
		close(f.done)
	}
	f.mu.Unlock()
	return 200, []byte(`{"result":"ok"}`), nil
}

func TestRunPullsAnItemAndPostsItsAnswer(t *testing.T) {
	store := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"data":[]}`))
	}))
	defer store.Close()
	sender := &fakeSender{items: []Item{item("clickhouse.query", map[string]any{"statement": "SELECT 1"})},
		done: make(chan struct{})}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go New(Config{ClickHouseURL: store.URL, Workers: 2}, sender, quietLogger()).Run(ctx)
	select {
	case <-sender.done:
	case <-time.After(3 * time.Second):
		t.Fatal("no answer posted")
	}
	sender.mu.Lock()
	defer sender.mu.Unlock()
	if got := sender.answers[0]; got.ID != "0123456789abcdef0123456789abcdef" || got.StatusCode != 200 {
		t.Fatalf("answer %+v", got)
	}
}

package logging

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLogOutputIsRedactedJSON(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "log.jsonl")
	f, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	logger := New("info", f)
	logger.Info("connect failed", "target", "postgres://drake:fakepw@db:5432/x", "attempt", 3)
	if err := f.Close(); err != nil {
		t.Fatal(err)
	}

	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	line := strings.TrimSpace(string(raw))
	if strings.Contains(line, "fakepw") {
		t.Fatalf("credential leaked into log: %s", line)
	}

	var payload map[string]any
	if err := json.Unmarshal([]byte(line), &payload); err != nil {
		t.Fatalf("log line is not JSON: %v", err)
	}
	if payload["msg"] != "connect failed" {
		t.Fatalf("unexpected msg: %v", payload["msg"])
	}
	if !strings.Contains(payload["target"].(string), "[REDACTED]") {
		t.Fatalf("expected redacted target, got %v", payload["target"])
	}
}

func TestDebugLevelFiltering(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "log.jsonl")
	f, _ := os.Create(path)
	logger := New("warn", f)
	logger.Info("should be filtered")
	logger.Warn("should appear")
	_ = f.Close()

	raw, _ := os.ReadFile(path)
	content := string(raw)
	if strings.Contains(content, "should be filtered") {
		t.Fatal("info line must be filtered at warn level")
	}
	if !strings.Contains(content, "should appear") {
		t.Fatal("warn line missing")
	}
}

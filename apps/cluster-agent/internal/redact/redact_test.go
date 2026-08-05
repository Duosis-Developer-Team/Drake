package redact

import (
	"strings"
	"testing"
)

func TestURLCredentialsMasked(t *testing.T) {
	out := String("dial redis://user:fakepw@cache:6379/0 failed")
	if strings.Contains(out, "fakepw") {
		t.Fatalf("credential leaked: %q", out)
	}
	if !strings.Contains(out, Redacted) {
		t.Fatalf("expected redaction marker in %q", out)
	}
}

func TestAssignmentsMasked(t *testing.T) {
	for _, in := range []string{"token=abc123", "password: hunter2", "api_key=zzz"} {
		if !ContainsCredentialShape(in) {
			t.Fatalf("expected %q to be detected", in)
		}
	}
}

func TestBearerMasked(t *testing.T) {
	out := String("header Bearer abcdefghijklmnop123")
	if strings.Contains(out, "abcdefghijklmnop123") {
		t.Fatalf("token leaked: %q", out)
	}
}

func TestPlainTextUntouched(t *testing.T) {
	in := "watch reconnected after 250ms"
	if String(in) != in {
		t.Fatalf("plain text must not change")
	}
	if ContainsCredentialShape(in) {
		t.Fatal("plain text must not be flagged")
	}
}

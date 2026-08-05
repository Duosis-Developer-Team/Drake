// Package logging configures structured JSON logging with redaction.
package logging

import (
	"log/slog"
	"os"

	"github.com/Duosis-Developer-Team/Drake/apps/cluster-agent/internal/redact"
)

// New returns a JSON slog.Logger at the given level. Every string attribute
// and message passes through credential redaction.
func New(level string, w *os.File) *slog.Logger {
	var slogLevel slog.Level
	switch level {
	case "debug":
		slogLevel = slog.LevelDebug
	case "warn":
		slogLevel = slog.LevelWarn
	case "error":
		slogLevel = slog.LevelError
	default:
		slogLevel = slog.LevelInfo
	}
	if w == nil {
		w = os.Stderr
	}
	handler := slog.NewJSONHandler(w, &slog.HandlerOptions{
		Level: slogLevel,
		ReplaceAttr: func(_ []string, attr slog.Attr) slog.Attr {
			if attr.Value.Kind() == slog.KindString {
				attr.Value = slog.StringValue(redact.String(attr.Value.String()))
			}
			return attr
		},
	})
	return slog.New(handler)
}

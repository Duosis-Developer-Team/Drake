// Package redact masks credential-shaped content before it can reach logs.
package redact

import "regexp"

// Redacted is the replacement marker.
const Redacted = "[REDACTED]"

var patterns = []*regexp.Regexp{
	// user:password@host in URLs
	regexp.MustCompile(`(://[^/\s@:]+:)[^@\s]+(@)`),
	// key=value / key: value credential assignments
	regexp.MustCompile(`(?i)(\b(?:password|passwd|pwd|token|secret|api[_-]?key|access[_-]?key)\b\s*[=:]\s*)\S+`),
	// bearer tokens
	regexp.MustCompile(`(?i)(\bbearer\s+)[a-zA-Z0-9._~+/=-]{8,}`),
}

// String masks credential-shaped substrings in s.
func String(s string) string {
	out := s
	out = patterns[0].ReplaceAllString(out, "${1}"+Redacted+"${2}")
	out = patterns[1].ReplaceAllString(out, "${1}"+Redacted)
	out = patterns[2].ReplaceAllString(out, "${1}"+Redacted)
	return out
}

// ContainsCredentialShape reports whether s matches any redaction pattern.
func ContainsCredentialShape(s string) bool {
	return String(s) != s
}

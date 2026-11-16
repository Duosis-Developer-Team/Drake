package inventory

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
)

func deploymentWith(containers ...any) *unstructured.Unstructured {
	return &unstructured.Unstructured{Object: map[string]any{
		"apiVersion": "apps/v1",
		"kind":       "Deployment",
		"metadata": map[string]any{
			"name": "core-service", "namespace": "hermes-test",
			"uid": "4e81288e-811c-491c-c0a9-2cfad46e2e05", "resourceVersion": "1",
		},
		"spec": map[string]any{
			"template": map[string]any{"spec": map[string]any{"containers": containers}},
		},
	}}
}

func probesOf(t *testing.T, obj *unstructured.Unstructured) []map[string]string {
	t.Helper()
	record := Normalize(
		schema.GroupVersionResource{Group: "apps", Version: "v1", Resource: "deployments"},
		obj, "2026-11-16T12:00:00Z",
	)
	raw, err := json.Marshal(record)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	var wire struct {
		SpecSummary map[string]json.RawMessage `json:"spec_summary"`
	}
	if err := json.Unmarshal(raw, &wire); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	var probes []map[string]string
	if value, ok := wire.SpecSummary["http_probes"]; ok {
		if err := json.Unmarshal(value, &probes); err != nil {
			t.Fatalf("http_probes is not a list of string maps: %s", value)
		}
	}
	return probes
}

func TestHTTPProbesAreReportedAsPathAndPort(t *testing.T) {
	got := probesOf(t, deploymentWith(map[string]any{
		"name":  "api",
		"image": "ghcr.io/duosis/core:1",
		"ports": []any{map[string]any{"name": "http", "containerPort": int64(8000)}},
		"livenessProbe": map[string]any{"httpGet": map[string]any{
			"path": "/health", "port": "http",
			"httpHeaders": []any{map[string]any{"name": "Authorization", "value": "Bearer x"}},
		}},
		"readinessProbe": map[string]any{"httpGet": map[string]any{
			"path": "/ready?token=abc", "port": int64(8000), "host": "10.0.0.1",
		}},
		"startupProbe": map[string]any{"tcpSocket": map[string]any{"port": int64(8000)}},
	}))
	want := []map[string]string{
		{"container": "api", "kind": "liveness", "path": "/health", "port": "8000"},
		{"container": "api", "kind": "readiness", "path": "/ready", "port": "8000"},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("probes = %#v, want %#v", got, want)
	}
}

func TestNoHeaderHostOrQueryEverReachesTheWire(t *testing.T) {
	got := probesOf(t, deploymentWith(map[string]any{
		"name": "api", "image": "x",
		"livenessProbe": map[string]any{"httpGet": map[string]any{
			"path": "/h?secret=s", "port": int64(80), "host": "internal.example",
			"httpHeaders": []any{map[string]any{"name": "X-Key", "value": "k"}},
		}},
	}))
	encoded, _ := json.Marshal(got)
	for _, leaked := range []string{"secret", "internal.example", "X-Key", "httpHeaders", "host"} {
		if strings.Contains(string(encoded), leaked) {
			t.Fatalf("%q leaked into %s", leaked, encoded)
		}
	}
}

func TestAProbeThatCannotBeResolvedIsLeftOut(t *testing.T) {
	got := probesOf(t, deploymentWith(map[string]any{
		"name": "api", "image": "x",
		"livenessProbe":  map[string]any{"httpGet": map[string]any{"path": "/h", "port": "unknown"}},
		"readinessProbe": map[string]any{"httpGet": map[string]any{"path": "/r", "port": int64(0)}},
		"startupProbe":   map[string]any{"exec": map[string]any{"command": []any{"true"}}},
	}))
	if got != nil {
		t.Fatalf("expected no probes, got %#v", got)
	}
}

func TestAnEmptyProbePathIsTheRoot(t *testing.T) {
	got := probesOf(t, deploymentWith(map[string]any{
		"name": "web", "image": "x",
		"readinessProbe": map[string]any{"httpGet": map[string]any{"port": int64(80)}},
	}))
	if len(got) != 1 || got[0]["path"] != "/" || got[0]["port"] != "80" {
		t.Fatalf("probes = %#v", got)
	}
}

func TestProbesAreBounded(t *testing.T) {
	containers := make([]any, 0, 12)
	for range 12 {
		probe := map[string]any{"httpGet": map[string]any{"path": "/" + strings.Repeat("p", 400), "port": int64(80)}}
		containers = append(containers, map[string]any{
			"name": "c", "image": "x",
			"livenessProbe": probe, "readinessProbe": probe, "startupProbe": probe,
		})
	}
	got := probesOf(t, deploymentWith(containers...))
	if len(got) != maxProbes {
		t.Fatalf("expected %d probes (8 containers x 3), got %d", maxProbes, len(got))
	}
	if len(got[0]["path"]) != maxProbePath {
		t.Fatalf("path not bounded: %d", len(got[0]["path"]))
	}
}

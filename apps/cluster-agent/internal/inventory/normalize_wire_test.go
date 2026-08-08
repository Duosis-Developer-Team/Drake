package inventory

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
)

// The Go half of the snapshot-page wire contract.
//
// This file exists because of a defect it would have caught. Sprint 8 taught
// the normalizer to report container name + image reference — the only way
// Drake can say which build is running — and changed nothing on the API side.
// The Go tests asserted the Go STRUCT, the Python tests built their own
// payload dicts, and nothing carried a real agent-shaped payload across the
// language boundary. Every snapshot page 422'd in a real cluster while both
// suites stayed green.
//
// So the assertion here is deliberately about the JSON, not the struct: what
// travels is what matters, and `[]map[string]string` marshals to a shape the
// receiving model either accepts or does not.
//
// The shared fixture at packages/contracts/fixtures/agent/snapshot-page.json
// is the single definition of that wire; the contracts package validates it
// against the schema, and apps/api/tests/test_agent_wire_contract.py proves
// the API accepts it. A change on any side the others do not follow fails.

func fixturePath(t *testing.T) string {
	t.Helper()
	// internal/inventory -> cluster-agent -> apps -> repository root
	return filepath.Join(
		"..", "..", "..", "..",
		"packages", "contracts", "fixtures", "agent", "snapshot-page.json",
	)
}

func TestWorkloadContainersMarshalToTheContractShape(t *testing.T) {
	obj := &unstructured.Unstructured{Object: map[string]any{
		"apiVersion": "apps/v1",
		"kind":       "Deployment",
		"metadata": map[string]any{
			"name": "core-api", "namespace": "alpha-dev",
			"uid": "4e81288e-811c-491c-c0a9-2cfad46e2e05", "resourceVersion": "812431",
			"generation": int64(7),
		},
		"spec": map[string]any{
			"replicas": int64(3),
			"template": map[string]any{"spec": map[string]any{"containers": []any{
				map[string]any{"name": "api", "image": "ghcr.io/duosis/core-api:1.4.2"},
			}}},
		},
	}}
	record := Normalize(
		schema.GroupVersionResource{Group: "apps", Version: "v1", Resource: "deployments"},
		obj, "2026-08-06T11:59:58Z",
	)

	raw, err := json.Marshal(record)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	var wire map[string]any
	if err := json.Unmarshal(raw, &wire); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}

	spec, ok := wire["spec_summary"].(map[string]any)
	if !ok {
		t.Fatalf("spec_summary missing: %#v", wire)
	}
	containers, ok := spec["containers"].([]any)
	if !ok {
		t.Fatalf("containers is not a JSON array: %#v", spec["containers"])
	}
	if len(containers) != 1 {
		t.Fatalf("expected one container, got %d", len(containers))
	}
	entry, ok := containers[0].(map[string]any)
	if !ok {
		t.Fatalf("container entry is not a JSON object: %#v", containers[0])
	}
	// Exactly the keys the API model permits. An extra key here would be
	// refused on the wire, so it must be caught on this side too.
	for key := range entry {
		if key != "name" && key != "image" && key != "image_id" {
			t.Fatalf("container entry carries an unexpected key %q", key)
		}
	}
	if entry["image"] != "ghcr.io/duosis/core-api:1.4.2" {
		t.Fatalf("image reference wrong: %#v", entry)
	}
}

func TestPodResolvedImagesMarshalToTheContractShape(t *testing.T) {
	obj := &unstructured.Unstructured{Object: map[string]any{
		"apiVersion": "v1",
		"kind":       "Pod",
		"metadata": map[string]any{
			"name": "core-api-7d9f6c8b4-x2knm", "namespace": "alpha-dev",
			"uid": "9a1c33d2-55b7-4a0e-b1c8-6f2ea0d41b77", "resourceVersion": "812455",
		},
		"spec": map[string]any{"nodeName": "node-1", "containers": []any{
			map[string]any{"name": "api"},
		}},
		"status": map[string]any{
			"phase": "Running",
			"containerStatuses": []any{map[string]any{
				"name":    "api",
				"image":   "ghcr.io/duosis/core-api:1.4.2",
				"imageID": "ghcr.io/duosis/core-api@sha256:3b1e0c9a7d5f",
			}},
		},
	}}
	record := Normalize(
		schema.GroupVersionResource{Version: "v1", Resource: "pods"}, obj, "2026-08-06T11:59:59Z",
	)

	raw, err := json.Marshal(record)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	var wire map[string]any
	if err := json.Unmarshal(raw, &wire); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	status, ok := wire["status_summary"].(map[string]any)
	if !ok {
		t.Fatalf("status_summary missing: %#v", wire)
	}
	images, ok := status["container_images"].([]any)
	if !ok {
		t.Fatalf("container_images is not a JSON array: %#v", status["container_images"])
	}
	entry, ok := images[0].(map[string]any)
	if !ok {
		t.Fatalf("image entry is not a JSON object: %#v", images[0])
	}
	// The resolved digest: what the node actually pulled, as opposed to what
	// the spec asked for. Sprint 8 exists to record this distinction.
	if entry["image_id"] != "ghcr.io/duosis/core-api@sha256:3b1e0c9a7d5f" {
		t.Fatalf("resolved image id wrong: %#v", entry)
	}
}

func TestTheSharedFixtureStillCarriesTheNestedShape(t *testing.T) {
	// A fixture that drifts back to scalars-only would make the contract
	// tests on all three sides pass against a wire nobody uses.
	raw, err := os.ReadFile(fixturePath(t))
	if err != nil {
		t.Skipf("shared fixture not readable from this checkout: %v", err)
	}
	var page struct {
		Resources []struct {
			Kind          string         `json:"kind"`
			SpecSummary   map[string]any `json:"spec_summary"`
			StatusSummary map[string]any `json:"status_summary"`
		} `json:"resources"`
	}
	if err := json.Unmarshal(raw, &page); err != nil {
		t.Fatalf("fixture is not valid JSON: %v", err)
	}

	var sawContainers, sawImages bool
	for _, resource := range page.Resources {
		if list, ok := resource.SpecSummary["containers"].([]any); ok && len(list) > 0 {
			sawContainers = true
		}
		if list, ok := resource.StatusSummary["container_images"].([]any); ok && len(list) > 0 {
			sawImages = true
		}
	}
	if !sawContainers {
		t.Fatal("shared fixture no longer exercises spec_summary.containers")
	}
	if !sawImages {
		t.Fatal("shared fixture no longer exercises status_summary.container_images")
	}
}

package inventory

import (
	"fmt"
	"strings"
	"testing"

	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
)

var podGVR = schema.GroupVersionResource{Group: "", Version: "v1", Resource: "pods"}
var deployGVR = schema.GroupVersionResource{Group: "apps", Version: "v1", Resource: "deployments"}

const observed = "2026-08-06T10:00:00Z"

func pod(name string, mutate func(map[string]any)) *unstructured.Unstructured {
	obj := map[string]any{
		"apiVersion": "v1",
		"kind":       "Pod",
		"metadata": map[string]any{
			"name":            name,
			"namespace":       "team-a",
			"uid":             "11111111-2222-3333-4444-555555555555",
			"resourceVersion": "42",
		},
	}
	if mutate != nil {
		mutate(obj)
	}
	return &unstructured.Unstructured{Object: obj}
}

func TestNormalizeIdentityFields(t *testing.T) {
	record := Normalize(podGVR, pod("web-1", nil), observed)
	if record.Kind != "Pod" || record.Name != "web-1" || record.UID == "" {
		t.Fatalf("identity fields wrong: %+v", record)
	}
	if record.Namespace == nil || *record.Namespace != "team-a" {
		t.Fatalf("namespace lost: %+v", record.Namespace)
	}
	if record.ObservedAt != observed {
		t.Fatalf("observed_at wrong: %q", record.ObservedAt)
	}
}

func TestRawManifestAnnotationNeverPasses(t *testing.T) {
	record := Normalize(podGVR, pod("web-1", func(obj map[string]any) {
		metadata := obj["metadata"].(map[string]any)
		metadata["annotations"] = map[string]any{
			"kubectl.kubernetes.io/last-applied-configuration": `{"apiVersion":"v1","data":{"pw":"hunter2"}}`,
			"kubernetes.io/description":                        "fine",
			"kubernetes.io/service-account-token":              "never",
			"app.kubernetes.io/some-secret-ref":                "never",
		}
	}), observed)
	for key := range record.Annotations {
		lowered := strings.ToLower(key)
		if strings.Contains(lowered, "last-applied") || strings.Contains(lowered, "token") ||
			strings.Contains(lowered, "secret") {
			t.Fatalf("forbidden annotation leaked: %q", key)
		}
	}
	if record.Annotations["kubernetes.io/description"] != "fine" {
		t.Fatalf("allowlisted annotation dropped: %+v", record.Annotations)
	}
}

func TestLabelBoundsEnforced(t *testing.T) {
	record := Normalize(podGVR, pod("web-1", func(obj map[string]any) {
		labels := map[string]any{}
		for index := range 60 {
			labels[fmt.Sprintf("app.kubernetes.io/l%03d", index)] = strings.Repeat("v", 4000)
		}
		labels["totally-custom-unreviewed-key"] = "dropped"
		obj["metadata"].(map[string]any)["labels"] = labels
	}), observed)
	if len(record.Labels) > 32 {
		t.Fatalf("label count unbounded: %d", len(record.Labels))
	}
	for key, value := range record.Labels {
		if len(value) > 512 {
			t.Fatalf("label value unbounded for %q: %d bytes", key, len(value))
		}
	}
	if _, leaked := record.Labels["totally-custom-unreviewed-key"]; leaked {
		t.Fatal("non-allowlisted label key leaked")
	}
}

func TestOwnersAndConditionsAreBounded(t *testing.T) {
	record := Normalize(deployGVR, &unstructured.Unstructured{Object: map[string]any{
		"apiVersion": "apps/v1",
		"kind":       "Deployment",
		"metadata": map[string]any{
			"name": "api", "namespace": "team-a",
			"uid": "21111111-2222-3333-4444-555555555555", "resourceVersion": "7",
			"ownerReferences": func() []any {
				owners := make([]any, 0, 12)
				for index := range 12 {
					owners = append(owners, map[string]any{
						"kind": "Thing", "name": fmt.Sprintf("owner-%d", index), "uid": "u",
					})
				}
				return owners
			}(),
		},
		"status": map[string]any{
			"conditions": func() []any {
				conditions := make([]any, 0, 20)
				for index := range 20 {
					conditions = append(conditions, map[string]any{
						"type": fmt.Sprintf("Cond%d", index), "status": "True",
						"message": strings.Repeat("m", 5000),
					})
				}
				return conditions
			}(),
		},
	}}, observed)
	if len(record.Owners) > 8 {
		t.Fatalf("owners unbounded: %d", len(record.Owners))
	}
	if len(record.Conditions) > 12 {
		t.Fatalf("conditions unbounded: %d", len(record.Conditions))
	}
	for _, condition := range record.Conditions {
		if len(condition.Message) > 256 {
			t.Fatalf("condition message unbounded: %d", len(condition.Message))
		}
	}
}

func TestSummariesRejectNestedStructures(t *testing.T) {
	record := Normalize(podGVR, pod("web-1", func(obj map[string]any) {
		obj["spec"] = map[string]any{
			"nodeName": "node-1",
			"containers": []any{map[string]any{
				"name": "app",
				"env":  []any{map[string]any{"name": "DB_PASSWORD", "value": "hunter2"}},
			}},
		}
		obj["status"] = map[string]any{"phase": "Running"}
	}), observed)
	for key, value := range record.SpecSummary {
		switch value.(type) {
		case string, int64, float64, bool, nil:
		default:
			t.Fatalf("nested structure leaked into spec summary at %q: %T", key, value)
		}
	}
	if record.SpecSummary["node"] != "node-1" {
		t.Fatalf("expected node in spec summary: %+v", record.SpecSummary)
	}
	if record.SpecSummary["containers"] != int64(1) {
		t.Fatalf("expected bounded container count: %+v", record.SpecSummary)
	}
	if record.StatusSummary["phase"] != "Running" {
		t.Fatalf("expected phase in status summary: %+v", record.StatusSummary)
	}
	encoded := fmt.Sprintf("%+v", record)
	if strings.Contains(encoded, "hunter2") || strings.Contains(encoded, "DB_PASSWORD") {
		t.Fatal("credential-shaped container env leaked through normalization")
	}
}

func TestPodContainerFactsSurfaceCrashLoopAndOOM(t *testing.T) {
	record := Normalize(podGVR, pod("web-1", func(obj map[string]any) {
		obj["status"] = map[string]any{
			"phase": "Running",
			"containerStatuses": []any{
				map[string]any{
					"restartCount": int64(3),
					"state": map[string]any{
						"waiting": map[string]any{"reason": "CrashLoopBackOff"},
					},
					"lastState": map[string]any{
						"terminated": map[string]any{"reason": "OOMKilled"},
					},
				},
				map[string]any{"restartCount": int64(2)},
			},
		}
	}), observed)
	if record.StatusSummary["restarts"] != int64(5) {
		t.Fatalf("restart sum wrong: %+v", record.StatusSummary)
	}
	if record.StatusSummary["crashloop"] != true || record.StatusSummary["oom_killed"] != true {
		t.Fatalf("crashloop/oom facts missing: %+v", record.StatusSummary)
	}
}

func TestAllowlistNeverContainsForbiddenKinds(t *testing.T) {
	for _, gvr := range append(append([]schema.GroupVersionResource{}, AllowedGVRs...), OptionalGVRs...) {
		if gvr.Resource == "secrets" || gvr.Resource == "configmaps" {
			t.Fatalf("forbidden resource in allowlist: %v", gvr)
		}
		if strings.Contains(gvr.Resource, "*") || strings.Contains(gvr.Group, "*") {
			t.Fatalf("wildcard in allowlist: %v", gvr)
		}
		if strings.Contains(gvr.Resource, "/") {
			t.Fatalf("subresource in allowlist: %v", gvr)
		}
	}
}

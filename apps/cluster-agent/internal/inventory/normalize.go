// Package inventory normalizes Kubernetes objects into the bounded
// contract shape (ADR-0018). Full manifests never leave this package:
// only identity, allowlisted labels/annotations, owner summaries, per-kind
// spec/status summaries, and bounded conditions.
package inventory

import (
	"sort"
	"strings"

	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
)

// AllowedGVRs is the exact collection allowlist (verbs: get/list/watch).
var AllowedGVRs = []schema.GroupVersionResource{
	{Group: "", Version: "v1", Resource: "namespaces"},
	{Group: "", Version: "v1", Resource: "nodes"},
	{Group: "", Version: "v1", Resource: "pods"},
	{Group: "", Version: "v1", Resource: "services"},
	{Group: "discovery.k8s.io", Version: "v1", Resource: "endpointslices"},
	{Group: "apps", Version: "v1", Resource: "deployments"},
	{Group: "apps", Version: "v1", Resource: "replicasets"},
	{Group: "apps", Version: "v1", Resource: "statefulsets"},
	{Group: "apps", Version: "v1", Resource: "daemonsets"},
	{Group: "batch", Version: "v1", Resource: "jobs"},
	{Group: "batch", Version: "v1", Resource: "cronjobs"},
	{Group: "", Version: "v1", Resource: "persistentvolumeclaims"},
	{Group: "", Version: "v1", Resource: "persistentvolumes"},
	{Group: "storage.k8s.io", Version: "v1", Resource: "storageclasses"},
	{Group: "autoscaling", Version: "v2", Resource: "horizontalpodautoscalers"},
	{Group: "policy", Version: "v1", Resource: "poddisruptionbudgets"},
	{Group: "", Version: "v1", Resource: "resourcequotas"},
	{Group: "", Version: "v1", Resource: "limitranges"},
	{Group: "", Version: "v1", Resource: "events"},
}

// OptionalGVRs are collected only when the CRD exists in the cluster.
var OptionalGVRs = []schema.GroupVersionResource{
	{Group: "monitoring.coreos.com", Version: "v1", Resource: "servicemonitors"},
	{Group: "monitoring.coreos.com", Version: "v1", Resource: "podmonitors"},
	{Group: "monitoring.coreos.com", Version: "v1", Resource: "prometheusrules"},
}

const (
	maxMapEntries = 32
	maxValueLen   = 512
	maxSummary    = 24
	maxConditions = 12
	maxOwners     = 8
)

// allowedKeyPrefixes bounds label/annotation cardinality to reviewed keys.
var allowedKeyPrefixes = []string{
	"app.kubernetes.io/",
	"kubernetes.io/",
	"k8s.io/",
	"drake.duosis.com/",
	"topology.kubernetes.io/",
	"node.kubernetes.io/",
	"batch.kubernetes.io/",
	"helm.sh/",
	"app",
	"component",
	"tier",
	"release",
}

// forbiddenAnnotationSubstrings never pass (raw manifests, credentials).
var forbiddenAnnotationSubstrings = []string{
	"last-applied-configuration",
	"token",
	"secret",
	"password",
	"credential",
}

// Resource is the bounded contract record.
type Resource struct {
	APIGroup        string            `json:"api_group"`
	APIVersion      string            `json:"api_version"`
	Kind            string            `json:"kind"`
	Namespace       *string           `json:"namespace,omitempty"`
	Name            string            `json:"name"`
	UID             string            `json:"uid"`
	ResourceVersion string            `json:"resource_version"`
	Labels          map[string]string `json:"labels,omitempty"`
	Annotations     map[string]string `json:"annotations,omitempty"`
	Owners          []OwnerRef        `json:"owners,omitempty"`
	SpecSummary     map[string]any    `json:"spec_summary,omitempty"`
	StatusSummary   map[string]any    `json:"status_summary,omitempty"`
	Conditions      []Condition       `json:"conditions,omitempty"`
	ObservedAt      string            `json:"observed_at"`
}

// OwnerRef is a bounded owner summary.
type OwnerRef struct {
	Kind string `json:"kind"`
	Name string `json:"name"`
	UID  string `json:"uid"`
}

// Condition is a bounded status condition.
type Condition struct {
	Type    string `json:"type"`
	Status  string `json:"status"`
	Reason  string `json:"reason,omitempty"`
	Message string `json:"message,omitempty"`
}

func keyAllowed(key string) bool {
	for _, prefix := range allowedKeyPrefixes {
		if strings.HasPrefix(key, prefix) {
			return true
		}
	}
	return false
}

func boundedMap(source map[string]string, annotations bool) map[string]string {
	if len(source) == 0 {
		return nil
	}
	keys := make([]string, 0, len(source))
	for key := range source {
		if !keyAllowed(key) {
			continue
		}
		if annotations {
			lowered := strings.ToLower(key)
			skip := false
			for _, forbidden := range forbiddenAnnotationSubstrings {
				if strings.Contains(lowered, forbidden) {
					skip = true
					break
				}
			}
			if skip {
				continue
			}
		}
		keys = append(keys, key)
	}
	sort.Strings(keys)
	if len(keys) > maxMapEntries {
		keys = keys[:maxMapEntries]
	}
	out := make(map[string]string, len(keys))
	for _, key := range keys {
		value := source[key]
		if len(value) > maxValueLen {
			value = value[:maxValueLen]
		}
		out[key] = value
	}
	if len(out) == 0 {
		return nil
	}
	return out
}

func truncate(value string, limit int) string {
	if len(value) > limit {
		return value[:limit]
	}
	return value
}

func summaryValue(value any) (any, bool) {
	switch typed := value.(type) {
	case string:
		return truncate(typed, 256), true
	case int64, float64, bool, nil:
		return typed, true
	default:
		return nil, false // nested structures never pass into summaries
	}
}

func putSummary(target map[string]any, key string, value any) {
	if len(target) >= maxSummary {
		return
	}
	if bounded, ok := summaryValue(value); ok {
		target[key] = bounded
	}
}

// Normalize converts one unstructured object into the bounded record.
func Normalize(gvr schema.GroupVersionResource, obj *unstructured.Unstructured, observedAt string) Resource {
	kind := obj.GetKind()
	record := Resource{
		APIGroup:        gvr.Group,
		APIVersion:      gvr.Version,
		Kind:            kind,
		Name:            truncate(obj.GetName(), 253),
		UID:             string(obj.GetUID()),
		ResourceVersion: truncate(obj.GetResourceVersion(), 64),
		Labels:          boundedMap(obj.GetLabels(), false),
		Annotations:     boundedMap(obj.GetAnnotations(), true),
		ObservedAt:      observedAt,
	}
	if ns := obj.GetNamespace(); ns != "" {
		namespace := truncate(ns, 63)
		record.Namespace = &namespace
	}
	for index, owner := range obj.GetOwnerReferences() {
		if index >= maxOwners {
			break
		}
		record.Owners = append(record.Owners, OwnerRef{
			Kind: truncate(owner.Kind, 64),
			Name: truncate(owner.Name, 253),
			UID:  truncate(string(owner.UID), 64),
		})
	}
	record.SpecSummary = specSummary(kind, obj)
	record.StatusSummary, record.Conditions = statusSummary(kind, obj)
	return record
}

func nested(obj *unstructured.Unstructured, fields ...string) (any, bool) {
	value, found, err := unstructured.NestedFieldNoCopy(obj.Object, fields...)
	if err != nil || !found {
		return nil, false
	}
	return value, true
}

func specSummary(kind string, obj *unstructured.Unstructured) map[string]any {
	out := map[string]any{}
	switch kind {
	case "Deployment", "ReplicaSet", "StatefulSet", "DaemonSet":
		if v, ok := nested(obj, "spec", "replicas"); ok {
			putSummary(out, "replicas", v)
		}
		// metadata.generation vs status.observedGeneration exposes rollout
		// lag deterministically — no name guessing involved.
		putSummary(out, "generation", obj.GetGeneration())
	case "CronJob":
		if v, ok := nested(obj, "spec", "schedule"); ok {
			putSummary(out, "schedule", v)
		}
		if v, ok := nested(obj, "spec", "suspend"); ok {
			putSummary(out, "suspend", v)
		}
	case "Service":
		if v, ok := nested(obj, "spec", "type"); ok {
			putSummary(out, "type", v)
		}
		if v, ok := nested(obj, "spec", "clusterIP"); ok {
			putSummary(out, "cluster_ip", v)
		}
	case "PersistentVolumeClaim":
		if v, ok := nested(obj, "spec", "storageClassName"); ok {
			putSummary(out, "storage_class", v)
		}
		if v, ok := nested(obj, "spec", "resources", "requests", "storage"); ok {
			putSummary(out, "requested_storage", v)
		}
	case "Node":
		if v, ok := nested(obj, "status", "nodeInfo", "kubeletVersion"); ok {
			putSummary(out, "kubelet_version", v)
		}
	case "Pod":
		if v, ok := nested(obj, "spec", "nodeName"); ok {
			putSummary(out, "node", v)
		}
		// Never: env vars, images with digests are fine but bounded:
		if containers, ok := nested(obj, "spec", "containers"); ok {
			if list, isList := containers.([]any); isList {
				putSummary(out, "containers", int64(len(list)))
			}
		}
	case "HorizontalPodAutoscaler":
		if v, ok := nested(obj, "spec", "minReplicas"); ok {
			putSummary(out, "min_replicas", v)
		}
		if v, ok := nested(obj, "spec", "maxReplicas"); ok {
			putSummary(out, "max_replicas", v)
		}
	}
	if len(out) == 0 {
		return nil
	}
	return out
}

func statusSummary(kind string, obj *unstructured.Unstructured) (map[string]any, []Condition) {
	out := map[string]any{}
	switch kind {
	case "Deployment":
		for source, target := range map[string]string{
			"replicas": "replicas", "availableReplicas": "available_replicas",
			"readyReplicas": "ready_replicas", "updatedReplicas": "updated_replicas",
			"unavailableReplicas": "unavailable_replicas",
			"observedGeneration":  "observed_generation",
		} {
			if v, ok := nested(obj, "status", source); ok {
				putSummary(out, target, v)
			}
		}
	case "StatefulSet", "ReplicaSet":
		for source, target := range map[string]string{
			"replicas": "replicas", "readyReplicas": "ready_replicas",
			"currentReplicas": "current_replicas", "updatedReplicas": "updated_replicas",
			"availableReplicas":  "available_replicas",
			"observedGeneration": "observed_generation",
			"currentRevision":    "current_revision",
			"updateRevision":     "update_revision",
		} {
			if v, ok := nested(obj, "status", source); ok {
				putSummary(out, target, v)
			}
		}
	case "DaemonSet":
		for source, target := range map[string]string{
			"desiredNumberScheduled": "desired", "numberReady": "ready",
			"numberAvailable": "available", "numberMisscheduled": "misscheduled",
			"observedGeneration": "observed_generation", "updatedNumberScheduled": "updated",
		} {
			if v, ok := nested(obj, "status", source); ok {
				putSummary(out, target, v)
			}
		}
	case "Pod":
		if v, ok := nested(obj, "status", "phase"); ok {
			putSummary(out, "phase", v)
		}
		if v, ok := nested(obj, "status", "reason"); ok {
			putSummary(out, "reason", v)
		}
		restarts, oom, crashloop, waitingReason := podContainerFacts(obj)
		putSummary(out, "restarts", restarts)
		if oom {
			putSummary(out, "oom_killed", true)
		}
		if crashloop {
			putSummary(out, "crashloop", true)
		}
		if waitingReason != "" {
			putSummary(out, "waiting_reason", waitingReason)
		}
	case "Job":
		for source, target := range map[string]string{
			"succeeded": "succeeded", "failed": "failed", "active": "active",
		} {
			if v, ok := nested(obj, "status", source); ok {
				putSummary(out, target, v)
			}
		}
	case "CronJob":
		if v, ok := nested(obj, "status", "lastScheduleTime"); ok {
			putSummary(out, "last_schedule_time", v)
		}
		if v, ok := nested(obj, "status", "lastSuccessfulTime"); ok {
			putSummary(out, "last_successful_time", v)
		}
	case "PersistentVolumeClaim", "PersistentVolume":
		if v, ok := nested(obj, "status", "phase"); ok {
			putSummary(out, "phase", v)
		}
	case "Namespace":
		if v, ok := nested(obj, "status", "phase"); ok {
			putSummary(out, "phase", v)
		}
	case "ResourceQuota":
		quotaSummary(obj, out)
	case "Event":
		if v, ok := nested(obj, "reason"); ok {
			putSummary(out, "reason", v)
		}
		if v, ok := nested(obj, "type"); ok {
			putSummary(out, "type", v)
		}
		if v, ok := nested(obj, "count"); ok {
			putSummary(out, "count", v)
		}
	}
	conditions := boundedConditions(obj)
	if len(out) == 0 {
		return nil, conditions
	}
	return out, conditions
}

// quotaSummary emits bounded hard/used pairs for a ResourceQuota: the
// first few sorted resource names, values as their canonical strings.
func quotaSummary(obj *unstructured.Unstructured, out map[string]any) {
	hardRaw, hardOK := nested(obj, "status", "hard")
	usedRaw, usedOK := nested(obj, "status", "used")
	if !hardOK || !usedOK {
		return
	}
	hard, hardIsMap := hardRaw.(map[string]any)
	used, usedIsMap := usedRaw.(map[string]any)
	if !hardIsMap || !usedIsMap {
		return
	}
	keys := make([]string, 0, len(hard))
	for key := range hard {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	if len(keys) > 8 {
		keys = keys[:8]
	}
	for _, key := range keys {
		safe := strings.ReplaceAll(key, "/", "_")
		if value, isStr := hard[key].(string); isStr {
			putSummary(out, "hard_"+safe, value)
		}
		if value, isStr := used[key].(string); isStr {
			putSummary(out, "used_"+safe, value)
		}
	}
}

func podContainerFacts(obj *unstructured.Unstructured) (int64, bool, bool, string) {
	var restarts int64
	var oom, crashloop bool
	waitingReason := ""
	statuses, ok := nested(obj, "status", "containerStatuses")
	if !ok {
		return 0, false, false, ""
	}
	list, isList := statuses.([]any)
	if !isList {
		return 0, false, false, ""
	}
	for _, item := range list {
		entry, isMap := item.(map[string]any)
		if !isMap {
			continue
		}
		if count, isNum := entry["restartCount"].(int64); isNum {
			restarts += count
		}
		if state, isMap := entry["state"].(map[string]any); isMap {
			if waiting, isMap := state["waiting"].(map[string]any); isMap {
				if reason, isStr := waiting["reason"].(string); isStr {
					waitingReason = truncate(reason, 64)
					if reason == "CrashLoopBackOff" {
						crashloop = true
					}
				}
			}
		}
		if last, isMap := entry["lastState"].(map[string]any); isMap {
			if terminated, isMap := last["terminated"].(map[string]any); isMap {
				if reason, isStr := terminated["reason"].(string); isStr && reason == "OOMKilled" {
					oom = true
				}
			}
		}
	}
	return restarts, oom, crashloop, waitingReason
}

func boundedConditions(obj *unstructured.Unstructured) []Condition {
	raw, ok := nested(obj, "status", "conditions")
	if !ok {
		return nil
	}
	list, isList := raw.([]any)
	if !isList {
		return nil
	}
	out := make([]Condition, 0, maxConditions)
	for _, item := range list {
		if len(out) >= maxConditions {
			break
		}
		entry, isMap := item.(map[string]any)
		if !isMap {
			continue
		}
		condition := Condition{}
		if v, isStr := entry["type"].(string); isStr {
			condition.Type = truncate(v, 64)
		}
		if v, isStr := entry["status"].(string); isStr {
			condition.Status = truncate(v, 16)
		}
		if v, isStr := entry["reason"].(string); isStr {
			condition.Reason = truncate(v, 128)
		}
		if v, isStr := entry["message"].(string); isStr {
			condition.Message = truncate(v, 256)
		}
		if condition.Type != "" && condition.Status != "" {
			out = append(out, condition)
		}
	}
	if len(out) == 0 {
		return nil
	}
	return out
}

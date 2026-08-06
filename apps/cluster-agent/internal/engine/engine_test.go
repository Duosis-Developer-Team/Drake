package engine

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/watch"
	dynfake "k8s.io/client-go/dynamic/fake"
	k8stesting "k8s.io/client-go/testing"

	"github.com/Duosis-Developer-Team/Drake/apps/cluster-agent/internal/inventory"
)

const (
	testClusterID = "5f0c9a4e-8f19-4a52-9d5e-2f6f5b3f9a11"
	testAgentID   = "6a1d8b5f-9e2a-4b63-8c7d-3a5b6c7d8e9f"
)

// recordingSender captures every message the engine sends.
type recordingSender struct {
	mu       sync.Mutex
	messages []sentMessage
	status   int
	err      error
}

type sentMessage struct {
	path    string
	payload map[string]any
}

func (r *recordingSender) Post(_ context.Context, path string, payload any) (int, []byte, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.err != nil {
		return 0, nil, r.err
	}
	r.messages = append(r.messages, sentMessage{path: path, payload: payload.(map[string]any)})
	status := r.status
	if status == 0 {
		status = 200
	}
	return status, []byte("{}"), nil
}

func (r *recordingSender) byKind(kind string) []sentMessage {
	r.mu.Lock()
	defer r.mu.Unlock()
	var out []sentMessage
	for _, message := range r.messages {
		if message.payload["kind"] == kind {
			out = append(out, message)
		}
	}
	return out
}

func (r *recordingSender) all() []sentMessage {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]sentMessage(nil), r.messages...)
}

// listKinds maps every collected GVR to its fake list kind.
func listKinds() map[schema.GroupVersionResource]string {
	kinds := map[schema.GroupVersionResource]string{}
	for _, gvr := range append(
		append([]schema.GroupVersionResource{}, inventory.AllowedGVRs...),
		inventory.OptionalGVRs...,
	) {
		singular := strings.TrimSuffix(gvr.Resource, "s")
		_ = singular
		kinds[gvr] = listKindFor(gvr)
	}
	return kinds
}

func listKindFor(gvr schema.GroupVersionResource) string {
	special := map[string]string{
		"endpointslices":           "EndpointSliceList",
		"persistentvolumeclaims":   "PersistentVolumeClaimList",
		"persistentvolumes":        "PersistentVolumeList",
		"storageclasses":           "StorageClassList",
		"horizontalpodautoscalers": "HorizontalPodAutoscalerList",
		"poddisruptionbudgets":     "PodDisruptionBudgetList",
		"resourcequotas":           "ResourceQuotaList",
		"limitranges":              "LimitRangeList",
		"servicemonitors":          "ServiceMonitorList",
		"podmonitors":              "PodMonitorList",
		"prometheusrules":          "PrometheusRuleList",
		"namespaces":               "NamespaceList",
		"nodes":                    "NodeList",
		"pods":                     "PodList",
		"services":                 "ServiceList",
		"deployments":              "DeploymentList",
		"replicasets":              "ReplicaSetList",
		"statefulsets":             "StatefulSetList",
		"daemonsets":               "DaemonSetList",
		"jobs":                     "JobList",
		"cronjobs":                 "CronJobList",
		"events":                   "EventList",
	}
	return special[gvr.Resource]
}

func testPod(name, uid string) *unstructured.Unstructured {
	return &unstructured.Unstructured{Object: map[string]any{
		"apiVersion": "v1",
		"kind":       "Pod",
		"metadata": map[string]any{
			"name":            name,
			"namespace":       "team-a",
			"uid":             uid,
			"resourceVersion": "10",
		},
	}}
}

func newTestEngine(t *testing.T, sender Sender, objects ...runtime.Object) (*Engine, *dynfake.FakeDynamicClient) {
	t.Helper()
	client := dynfake.NewSimpleDynamicClientWithCustomListKinds(
		runtime.NewScheme(), listKinds(), objects...,
	)
	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError}))
	built, err := New(Options{
		ClusterID:         testClusterID,
		AgentID:           testAgentID,
		AgentVersion:      "0.0.0-test",
		Dynamic:           client,
		Sender:            sender,
		Logger:            logger,
		HeartbeatInterval: time.Hour,
	})
	if err != nil {
		t.Fatalf("engine: %v", err)
	}
	return built, client
}

func TestSnapshotStreamsBeginPagesComplete(t *testing.T) {
	sender := &recordingSender{}
	engine, _ := newTestEngine(t, sender,
		testPod("web-1", "11111111-0000-0000-0000-000000000001"),
		testPod("web-2", "11111111-0000-0000-0000-000000000002"),
	)
	if _, err := engine.snapshot(context.Background()); err != nil {
		t.Fatalf("snapshot: %v", err)
	}

	begins := sender.byKind("snapshot_begin")
	pages := sender.byKind("snapshot_page")
	completes := sender.byKind("snapshot_complete")
	if len(begins) != 1 || len(completes) != 1 {
		t.Fatalf("expected exactly one begin and complete, got %d/%d", len(begins), len(completes))
	}
	if len(pages) != 1 {
		t.Fatalf("expected one page for two pods, got %d", len(pages))
	}
	resources := pages[0].payload["resources"].([]inventory.Resource)
	if len(resources) != 2 {
		t.Fatalf("expected 2 resources, got %d", len(resources))
	}
	if completes[0].payload["total_resources"] != 2 || completes[0].payload["total_pages"] != 1 {
		t.Fatalf("complete totals wrong: %+v", completes[0].payload)
	}
	if begins[0].payload["snapshot_uid"] != completes[0].payload["snapshot_uid"] {
		t.Fatal("snapshot uid must be stable across begin/page/complete")
	}
}

func TestSequencesAreStrictlyMonotonic(t *testing.T) {
	sender := &recordingSender{}
	engine, _ := newTestEngine(t, sender,
		testPod("web-1", "11111111-0000-0000-0000-000000000001"),
	)
	if _, err := engine.snapshot(context.Background()); err != nil {
		t.Fatalf("snapshot: %v", err)
	}
	if err := engine.sendHeartbeat(context.Background()); err != nil {
		t.Fatalf("heartbeat: %v", err)
	}
	previous := int64(0)
	for _, message := range sender.all() {
		sequence := message.payload["sequence"].(int64)
		if message.payload["kind"] == "heartbeat" {
			// Heartbeats report the counter without consuming a number —
			// they must never punch gaps into the inventory chain.
			if sequence != previous {
				t.Fatalf("heartbeat consumed a sequence number: %d after %d", sequence, previous)
			}
			continue
		}
		if sequence != previous+1 {
			t.Fatalf("inventory chain not gapless: %d after %d (%s)",
				sequence, previous, message.payload["kind"])
		}
		previous = sequence
	}
}

func TestSnapshotPagesNeverExceedBound(t *testing.T) {
	objects := make([]runtime.Object, 0, 600)
	for index := range 600 {
		objects = append(objects, testPod(
			fmt.Sprintf("pod-%03d", index),
			fmt.Sprintf("11111111-0000-0000-0000-%012d", index),
		))
	}
	sender := &recordingSender{}
	engine, _ := newTestEngine(t, sender, objects...)
	if _, err := engine.snapshot(context.Background()); err != nil {
		t.Fatalf("snapshot: %v", err)
	}
	pages := sender.byKind("snapshot_page")
	if len(pages) < 2 {
		t.Fatalf("expected 600 pods to need at least 2 pages, got %d", len(pages))
	}
	total := 0
	for _, page := range pages {
		count := len(page.payload["resources"].([]inventory.Resource))
		if count > 500 {
			t.Fatalf("page exceeds 500-resource bound: %d", count)
		}
		total += count
	}
	if total != 600 {
		t.Fatalf("resources lost in paging: %d", total)
	}
}

func TestWatchEventsFlowToServer(t *testing.T) {
	sender := &recordingSender{}
	engine, client := newTestEngine(t, sender)
	podGVR := schema.GroupVersionResource{Group: "", Version: "v1", Resource: "pods"}

	fakeWatcher := watch.NewFake()
	client.PrependWatchReactor("pods", k8stesting.DefaultWatchReactor(fakeWatcher, nil))

	engine.queue = make(chan changeEvent, 16)
	engine.overflow = make(chan struct{})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	done := make(chan error, 1)
	go func() { done <- engine.watchGVR(ctx, podGVR, "10") }()
	go fakeWatcher.Add(testPod("web-new", "11111111-0000-0000-0000-000000000009"))

	select {
	case change := <-engine.queue:
		if change.changeType != "added" || change.resource.Name != "web-new" {
			t.Fatalf("unexpected change: %+v", change)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("watch event never reached the queue")
	}
	cancel()
	if err := <-done; err != nil {
		t.Fatalf("watch should end quietly on cancel, got %v", err)
	}
}

func TestWatch410DemandsReconcile(t *testing.T) {
	sender := &recordingSender{}
	engine, client := newTestEngine(t, sender)
	podGVR := schema.GroupVersionResource{Group: "", Version: "v1", Resource: "pods"}

	fakeWatcher := watch.NewFake()
	client.PrependWatchReactor("pods", k8stesting.DefaultWatchReactor(fakeWatcher, nil))

	engine.queue = make(chan changeEvent, 16)
	engine.overflow = make(chan struct{})
	done := make(chan error, 1)
	go func() { done <- engine.watchGVR(context.Background(), podGVR, "10") }()
	go fakeWatcher.Error(&metav1.Status{
		Code:   410,
		Reason: metav1.StatusReasonExpired,
	})

	select {
	case err := <-done:
		if err == nil || !strings.Contains(err.Error(), "410") {
			t.Fatalf("expected a 410 reconcile error, got %v", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("410 never surfaced")
	}
}

func TestQueueOverflowDropsAndDemandsReconcile(t *testing.T) {
	sender := &recordingSender{}
	engine, client := newTestEngine(t, sender)
	podGVR := schema.GroupVersionResource{Group: "", Version: "v1", Resource: "pods"}

	fakeWatcher := watch.NewFakeWithChanSize(8, false)
	client.PrependWatchReactor("pods", k8stesting.DefaultWatchReactor(fakeWatcher, nil))

	// A deliberately tiny queue with NO consumer: the third event overflows.
	engine.queue = make(chan changeEvent, 2)
	engine.overflow = make(chan struct{})
	done := make(chan error, 1)
	go func() { done <- engine.watchGVR(context.Background(), podGVR, "10") }()
	for index := range 3 {
		fakeWatcher.Add(testPod(
			fmt.Sprintf("burst-%d", index),
			fmt.Sprintf("11111111-0000-0000-0000-00000000010%d", index),
		))
	}

	select {
	case err := <-done:
		if err == nil || !strings.Contains(err.Error(), "overflow") {
			t.Fatalf("expected overflow reconcile error, got %v", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("overflow never surfaced")
	}
	select {
	case <-engine.overflow:
	default:
		t.Fatal("overflow signal not raised")
	}
}

func TestSyncCycleRecoversStateTransitions(t *testing.T) {
	sender := &recordingSender{}
	engine, client := newTestEngine(t, sender,
		testPod("web-1", "11111111-0000-0000-0000-000000000001"),
	)
	fakeWatcher := watch.NewFake()
	client.PrependWatchReactor("*", k8stesting.DefaultWatchReactor(fakeWatcher, nil))

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- engine.syncCycle(ctx) }()

	deadline := time.After(5 * time.Second)
	for engine.InventoryState() != "fresh" {
		select {
		case <-deadline:
			t.Fatalf("state never reached fresh, at %q", engine.InventoryState())
		case <-time.After(10 * time.Millisecond):
		}
	}
	cancel()
	if err := <-done; err != nil && err != context.Canceled {
		t.Fatalf("cancelled cycle should end quietly, got %v", err)
	}
}

func TestHeartbeatCarriesInventoryState(t *testing.T) {
	sender := &recordingSender{}
	engine, _ := newTestEngine(t, sender)
	engine.setInventoryState("reconcile_required")
	if err := engine.sendHeartbeat(context.Background()); err != nil {
		t.Fatalf("heartbeat: %v", err)
	}
	beats := sender.byKind("heartbeat")
	if len(beats) != 1 {
		t.Fatalf("expected one heartbeat, got %d", len(beats))
	}
	payload := beats[0].payload
	if payload["inventory_state"] != "reconcile_required" {
		t.Fatalf("heartbeat must carry the real inventory state: %+v", payload)
	}
	for _, field := range []string{
		"api_version", "cluster_id", "agent_id", "agent_version",
		"request_id", "source_time", "sequence",
	} {
		if _, present := payload[field]; !present {
			t.Fatalf("heartbeat missing contract field %q", field)
		}
	}
	if !strings.HasSuffix(payload["source_time"].(string), "Z") {
		t.Fatalf("source_time must be UTC: %v", payload["source_time"])
	}
}

func TestOptionalCRDsOnlyCollectedWhenPresent(t *testing.T) {
	sender := &recordingSender{}
	engine, _ := newTestEngine(t, sender)

	withoutCRDs := len(engine.gvrs())
	engine.opts.CRDPresent = func(schema.GroupVersionResource) bool { return true }
	withCRDs := len(engine.gvrs())

	if withoutCRDs != len(inventory.AllowedGVRs) {
		t.Fatalf("absent CRDs must not be collected: %d", withoutCRDs)
	}
	if withCRDs != len(inventory.AllowedGVRs)+len(inventory.OptionalGVRs) {
		t.Fatalf("present CRDs must be collected: %d", withCRDs)
	}
}

func TestRunStopsCleanlyOnCancel(t *testing.T) {
	sender := &recordingSender{}
	engine, client := newTestEngine(t, sender)
	fakeWatcher := watch.NewFake()
	client.PrependWatchReactor("*", k8stesting.DefaultWatchReactor(fakeWatcher, nil))

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- engine.Run(ctx) }()

	deadline := time.After(5 * time.Second)
	for engine.InventoryState() != "fresh" {
		select {
		case <-deadline:
			t.Fatalf("engine never reached fresh, at %q", engine.InventoryState())
		case <-time.After(10 * time.Millisecond):
		}
	}
	cancel()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("Run leaked goroutines after cancel")
	}
}

func TestRenewalDelayTargetsTwoThirdsLifetime(t *testing.T) {
	now := time.Date(2026, 8, 6, 12, 0, 0, 0, time.UTC)
	notAfter := now.Add(14 * 24 * time.Hour)
	for range 50 {
		delay := renewalDelay(now, notAfter)
		lifetime := notAfter.Sub(now)
		if delay < lifetime/2 || delay > lifetime*4/5 {
			t.Fatalf("delay outside jittered 2/3 band: %v of %v", delay, lifetime)
		}
	}
	if delay := renewalDelay(now, now.Add(-time.Hour)); delay > time.Second {
		t.Fatalf("expired cert must renew immediately, got %v", delay)
	}
}

func TestSequencePersistsOnlyAfterAck(t *testing.T) {
	sender := &recordingSender{}
	engine, _ := newTestEngine(t, sender,
		testPod("web-1", "11111111-0000-0000-0000-000000000001"),
	)
	var stored []int64
	engine.opts.LoadSequence = func() int64 { return 41 }
	engine.opts.StoreSequence = func(value int64) error {
		stored = append(stored, value)
		return nil
	}
	engine.sequence = engine.opts.LoadSequence()

	if _, err := engine.snapshot(context.Background()); err != nil {
		t.Fatalf("snapshot: %v", err)
	}
	if err := engine.sendHeartbeat(context.Background()); err != nil {
		t.Fatalf("heartbeat: %v", err)
	}

	// Resumed from the persisted counter: the first message is 42.
	first := sender.all()[0]
	if first.payload["sequence"].(int64) != 42 {
		t.Fatalf("engine must resume from the persisted sequence, got %v",
			first.payload["sequence"])
	}
	// Every ACKed inventory message persisted, in order; heartbeats never.
	if len(stored) != 3 { // begin + page + complete
		t.Fatalf("expected 3 persisted ACKs, got %d (%v)", len(stored), stored)
	}
	for index, value := range stored {
		if value != int64(42+index) {
			t.Fatalf("ACK persistence out of order: %v", stored)
		}
	}
}

func TestFailedSendDoesNotPersistSequence(t *testing.T) {
	sender := &recordingSender{status: 409}
	engine, _ := newTestEngine(t, sender,
		testPod("web-1", "11111111-0000-0000-0000-000000000001"),
	)
	var stored []int64
	engine.opts.StoreSequence = func(value int64) error {
		stored = append(stored, value)
		return nil
	}
	if _, err := engine.snapshot(context.Background()); err == nil {
		t.Fatal("rejected snapshot must surface an error")
	}
	if len(stored) != 0 {
		t.Fatalf("refused messages must never advance the persisted sequence: %v", stored)
	}
}

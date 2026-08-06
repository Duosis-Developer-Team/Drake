// Package engine drives read-only Kubernetes discovery (ADR-0017).
//
// Order of operations per sync cycle: paginated LIST over the exact GVR
// allowlist → capture each list's resourceVersion → stream an atomic
// snapshot (begin / pages / complete) → WATCH from the captured versions so
// no change between LIST and WATCH is lost. A 410 Gone, a watch disconnect,
// a queue overflow, or a server-side reconcile_required all funnel into the
// same answer: bounded full reconcile with jittered backoff. Every message
// carries a monotonic sequence; the server treats gaps as reconcile setup.
package engine

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"math/rand"
	"net/http"
	"sync"
	"time"

	"github.com/google/uuid"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/watch"
	"k8s.io/client-go/dynamic"

	"github.com/Duosis-Developer-Team/Drake/apps/cluster-agent/internal/inventory"
	"github.com/Duosis-Developer-Team/Drake/apps/cluster-agent/internal/transport"
)

const (
	apiVersion      = "drake.duosis.com/agent/v1"
	listPageSize    = 250
	snapshotPageMax = 500
	watchBatchMax   = 500
	queueCapacity   = 2048
	flushInterval   = 2 * time.Second
	maxBackoff      = 2 * time.Minute
)

// Sender posts one payload to the internal agent API (see transport.Client).
type Sender interface {
	Post(ctx context.Context, path string, payload any) (int, []byte, error)
}

// CRDPresent reports whether an optional CRD group/version exists. Optional
// GVRs are only collected when their CRD is installed.
type CRDPresent func(gvr schema.GroupVersionResource) bool

// Clock abstracts time for deterministic tests.
type Clock func() time.Time

// Options wires the engine.
type Options struct {
	ClusterID    string
	AgentID      string
	AgentVersion string
	Dynamic      dynamic.Interface
	Sender       Sender
	CRDPresent   CRDPresent
	Logger       *slog.Logger
	// HeartbeatInterval defaults to 30s.
	HeartbeatInterval time.Duration
	// Now defaults to time.Now; injected in tests.
	Now Clock
	// LoadSequence/StoreSequence persist the last ACKed inventory
	// sequence next to the identity: restarts resume the chain instead of
	// re-basing blindly, and the counter only advances past a sequence
	// AFTER the server acknowledged it (crash between ACK and persist
	// replays an idempotent message).
	LoadSequence  func() int64
	StoreSequence func(int64) error
}

type changeEvent struct {
	changeType string
	resource   inventory.Resource
}

// Engine owns the sync lifecycle for one cluster.
type Engine struct {
	opts Options

	mu             sync.Mutex
	sequence       int64
	inventoryState string

	queue    chan changeEvent
	overflow chan struct{} // closed once per cycle on queue overflow
}

// New builds an Engine; fail-closed on incomplete wiring.
func New(opts Options) (*Engine, error) {
	if opts.ClusterID == "" || opts.AgentID == "" || opts.AgentVersion == "" {
		return nil, fmt.Errorf("cluster id, agent id, and agent version are required")
	}
	if opts.Dynamic == nil || opts.Sender == nil || opts.Logger == nil {
		return nil, fmt.Errorf("dynamic client, sender, and logger are required")
	}
	if opts.CRDPresent == nil {
		opts.CRDPresent = func(schema.GroupVersionResource) bool { return false }
	}
	if opts.HeartbeatInterval <= 0 {
		opts.HeartbeatInterval = 30 * time.Second
	}
	if opts.Now == nil {
		opts.Now = time.Now
	}
	engine := &Engine{opts: opts, inventoryState: "empty"}
	if opts.LoadSequence != nil {
		engine.sequence = opts.LoadSequence()
	}
	return engine, nil
}

// InventoryState returns the current freshness state (for the heartbeat).
func (e *Engine) InventoryState() string {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.inventoryState
}

func (e *Engine) setInventoryState(state string) {
	e.mu.Lock()
	e.inventoryState = state
	e.mu.Unlock()
}

// nextSequence hands out the monotonic INVENTORY message sequence. Only
// snapshot and watch messages consume numbers — they are serialized within
// a sync cycle, so the server can require a strict, gapless chain.
func (e *Engine) nextSequence() int64 {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.sequence++
	return e.sequence
}

// currentSequence reads the counter without consuming a number; heartbeats
// report it for observability but never advance the inventory chain (they
// run on their own goroutine and would otherwise punch fake gaps into it).
func (e *Engine) currentSequence() int64 {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.sequence
}

func (e *Engine) sourceTime() string {
	return e.opts.Now().UTC().Format(time.RFC3339)
}

func (e *Engine) payload(kind string, sequence int64) map[string]any {
	return map[string]any{
		"api_version":   apiVersion,
		"kind":          kind,
		"cluster_id":    e.opts.ClusterID,
		"agent_id":      e.opts.AgentID,
		"agent_version": e.opts.AgentVersion,
		"request_id":    uuid.NewString(),
		"source_time":   e.sourceTime(),
		"sequence":      sequence,
	}
}

// base consumes the next inventory sequence number.
func (e *Engine) base(kind string) map[string]any {
	return e.payload(kind, e.nextSequence())
}

func (e *Engine) post(ctx context.Context, path string, payload map[string]any) error {
	status, _, err := e.opts.Sender.Post(ctx, path, payload)
	if err != nil {
		return err
	}
	if status >= http.StatusBadRequest {
		return fmt.Errorf("server rejected %s with status %d", path, status)
	}
	e.persistAck(payload)
	return nil
}

// persistAck records the highest server-ACKed inventory sequence.
// Heartbeats never consume numbers, so they never persist either.
func (e *Engine) persistAck(payload map[string]any) {
	if e.opts.StoreSequence == nil || payload["kind"] == "heartbeat" {
		return
	}
	if sequence, ok := payload["sequence"].(int64); ok {
		if err := e.opts.StoreSequence(sequence); err != nil {
			e.opts.Logger.Warn("sequence persist failed", "error", err.Error())
		}
	}
}

// Run drives heartbeats and the sync loop until the context ends. All
// goroutines exit before Run returns.
func (e *Engine) Run(ctx context.Context) error {
	var group sync.WaitGroup
	group.Add(1)
	go func() {
		defer group.Done()
		e.heartbeatLoop(ctx)
	}()

	backoff := time.Second
	for ctx.Err() == nil {
		err := e.syncCycle(ctx)
		if err == nil || ctx.Err() != nil {
			break
		}
		e.opts.Logger.Warn("sync cycle ended; scheduling full reconcile", "error", err.Error())
		e.setInventoryState("reconcile_required")
		jitter := time.Duration(rand.Int63n(int64(backoff/2) + 1)) //nolint:gosec // jitter only
		select {
		case <-ctx.Done():
		case <-time.After(backoff + jitter):
		}
		backoff = min(backoff*2, maxBackoff)
	}
	group.Wait()
	return ctx.Err()
}

// syncCycle runs one snapshot + watch session; any error means reconcile.
func (e *Engine) syncCycle(ctx context.Context) error {
	e.queue = make(chan changeEvent, queueCapacity)
	e.overflow = make(chan struct{})

	e.setInventoryState("reconciling")
	versions, err := e.snapshot(ctx)
	if err != nil {
		return err
	}
	e.setInventoryState("fresh")
	return e.watchAll(ctx, versions)
}

// gvrs is the active collection set: the fixed allowlist plus optional CRDs
// that are actually installed. Nothing else is ever requested.
func (e *Engine) gvrs() []schema.GroupVersionResource {
	out := make([]schema.GroupVersionResource, 0,
		len(inventory.AllowedGVRs)+len(inventory.OptionalGVRs))
	out = append(out, inventory.AllowedGVRs...)
	for _, gvr := range inventory.OptionalGVRs {
		if e.opts.CRDPresent(gvr) {
			out = append(out, gvr)
		}
	}
	return out
}

// snapshot streams a full atomic snapshot and returns the resourceVersion
// captured per GVR — the exact point WATCH resumes from (no gap).
func (e *Engine) snapshot(ctx context.Context) (map[schema.GroupVersionResource]string, error) {
	snapshotUID := uuid.NewString()
	begin := e.base("snapshot_begin")
	begin["snapshot_uid"] = snapshotUID
	if err := e.post(ctx, "/internal/v1/agent/inventory/snapshot/begin", begin); err != nil {
		return nil, fmt.Errorf("snapshot begin: %w", err)
	}

	versions := map[schema.GroupVersionResource]string{}
	pageNumber := 0
	totalResources := 0
	var pending []inventory.Resource

	flush := func() error {
		if len(pending) == 0 {
			return nil
		}
		pageNumber++
		page := e.base("snapshot_page")
		page["snapshot_uid"] = snapshotUID
		page["page_number"] = pageNumber
		page["resources"] = pending
		if err := e.post(ctx, "/internal/v1/agent/inventory/snapshot/page", page); err != nil {
			return fmt.Errorf("snapshot page %d: %w", pageNumber, err)
		}
		totalResources += len(pending)
		pending = nil
		return nil
	}

	for _, gvr := range e.gvrs() {
		version, err := e.listGVR(ctx, gvr, func(resource inventory.Resource) error {
			pending = append(pending, resource)
			if len(pending) >= snapshotPageMax {
				return flush()
			}
			return nil
		})
		if err != nil {
			return nil, fmt.Errorf("list %s: %w", gvr.Resource, err)
		}
		versions[gvr] = version
	}
	if err := flush(); err != nil {
		return nil, err
	}

	complete := e.base("snapshot_complete")
	complete["snapshot_uid"] = snapshotUID
	complete["total_pages"] = pageNumber
	complete["total_resources"] = totalResources
	if err := e.post(ctx, "/internal/v1/agent/inventory/snapshot/complete", complete); err != nil {
		return nil, fmt.Errorf("snapshot complete: %w", err)
	}
	e.opts.Logger.Info("snapshot streamed",
		"pages", pageNumber, "resources", totalResources)
	return versions, nil
}

// listGVR pages through one collection and returns the list resourceVersion.
func (e *Engine) listGVR(
	ctx context.Context,
	gvr schema.GroupVersionResource,
	emit func(inventory.Resource) error,
) (string, error) {
	observedAt := e.sourceTime()
	continueToken := ""
	resourceVersion := ""
	for {
		list, err := e.opts.Dynamic.Resource(gvr).List(ctx, metav1.ListOptions{
			Limit:    listPageSize,
			Continue: continueToken,
		})
		if err != nil {
			return "", err
		}
		resourceVersion = list.GetResourceVersion()
		for index := range list.Items {
			if err := emit(inventory.Normalize(gvr, &list.Items[index], observedAt)); err != nil {
				return "", err
			}
		}
		continueToken = list.GetContinue()
		if continueToken == "" {
			return resourceVersion, nil
		}
	}
}

// watchAll runs one watcher per GVR plus the batch flusher. The first
// terminal condition (410, disconnect, overflow, server reconcile demand)
// cancels the session; all goroutines drain before returning.
func (e *Engine) watchAll(
	ctx context.Context, versions map[schema.GroupVersionResource]string,
) error {
	sessionCtx, cancel := context.WithCancel(ctx)
	defer cancel()

	failures := make(chan error, len(versions)+1)
	var group sync.WaitGroup
	for gvr, version := range versions {
		group.Add(1)
		go func(gvr schema.GroupVersionResource, version string) {
			defer group.Done()
			if err := e.watchGVR(sessionCtx, gvr, version); err != nil {
				failures <- fmt.Errorf("watch %s: %w", gvr.Resource, err)
				cancel()
			}
		}(gvr, version)
	}
	group.Add(1)
	go func() {
		defer group.Done()
		if err := e.flushLoop(sessionCtx); err != nil {
			failures <- err
			cancel()
		}
	}()

	group.Wait()
	close(failures)
	if err := <-failures; err != nil && ctx.Err() == nil {
		return err
	}
	return ctx.Err()
}

// watchGVR consumes one watch stream into the bounded queue. Overflow drops
// the event and demands a reconcile — the queue never grows unbounded.
func (e *Engine) watchGVR(
	ctx context.Context, gvr schema.GroupVersionResource, resourceVersion string,
) error {
	watcher, err := e.opts.Dynamic.Resource(gvr).Watch(ctx, metav1.ListOptions{
		ResourceVersion:     resourceVersion,
		AllowWatchBookmarks: true,
	})
	if err != nil {
		return err
	}
	defer watcher.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case event, open := <-watcher.ResultChan():
			if !open {
				return fmt.Errorf("watch stream closed")
			}
			switch event.Type {
			case watch.Bookmark:
				continue
			case watch.Error:
				status := apierrors.FromObject(event.Object)
				if apierrors.IsResourceExpired(status) || apierrors.IsGone(status) {
					return fmt.Errorf("resource version expired (410): %w", status)
				}
				return fmt.Errorf("watch error: %w", status)
			}
			obj, isUnstructured := event.Object.(*unstructured.Unstructured)
			if !isUnstructured {
				continue
			}
			change := changeEvent{
				changeType: watchChangeType(event.Type),
				resource:   inventory.Normalize(gvr, obj, e.sourceTime()),
			}
			select {
			case e.queue <- change:
			default:
				// Queue full: drop and force a bounded full reconcile.
				select {
				case <-e.overflow:
				default:
					close(e.overflow)
				}
				return fmt.Errorf("event queue overflow; reconcile required")
			}
		}
	}
}

func watchChangeType(eventType watch.EventType) string {
	switch eventType {
	case watch.Added:
		return "added"
	case watch.Deleted:
		return "deleted"
	default:
		return "updated"
	}
}

// flushLoop batches queued events into watch_events messages. A server
// reconcile demand surfaces as an error so the cycle restarts cleanly.
func (e *Engine) flushLoop(ctx context.Context) error {
	ticker := time.NewTicker(flushInterval)
	defer ticker.Stop()
	var batch []changeEvent
	flush := func() error {
		if len(batch) == 0 {
			return nil
		}
		events := make([]map[string]any, 0, len(batch))
		for _, change := range batch {
			events = append(events, map[string]any{
				"event_id":    uuid.NewString(),
				"change_type": change.changeType,
				"resource":    change.resource,
			})
		}
		payload := e.base("watch_events")
		payload["events"] = events
		batch = nil
		err := e.post(ctx, "/internal/v1/agent/inventory/events", payload)
		if errors.Is(err, transport.ErrReconcileRequired) {
			return fmt.Errorf("server demands reconcile: %w", err)
		}
		return err
	}
	for {
		select {
		case <-ctx.Done():
			return nil
		case change := <-e.queue:
			batch = append(batch, change)
			if len(batch) >= watchBatchMax {
				if err := flush(); err != nil {
					return err
				}
			}
		case <-ticker.C:
			if err := flush(); err != nil {
				return err
			}
		}
	}
}

// heartbeatLoop reports liveness + inventory state; failures only log —
// heartbeat is a signal, never a control path.
func (e *Engine) heartbeatLoop(ctx context.Context) {
	ticker := time.NewTicker(e.opts.HeartbeatInterval)
	defer ticker.Stop()
	for {
		if err := e.sendHeartbeat(ctx); err != nil && ctx.Err() == nil {
			e.opts.Logger.Warn("heartbeat failed", "error", err.Error())
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func (e *Engine) sendHeartbeat(ctx context.Context) error {
	// Heartbeat reports the current sequence without consuming a number.
	payload := e.payload("heartbeat", e.currentSequence())
	payload["inventory_state"] = e.InventoryState()
	return e.post(ctx, "/internal/v1/agent/heartbeat", payload)
}

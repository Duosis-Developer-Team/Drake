import { describe, expect, it } from "vitest";

import {
  alertEvents,
  buildTimeline,
  deploymentEvents,
  incidentEvents,
} from "@/lib/view-models/timeline";
import type { AlertInstance } from "@/lib/alerting";
import type { DeploymentRow } from "@/lib/deployments";
import type { IncidentSummary } from "@/lib/incidents";

function incident(overrides: Partial<IncidentSummary> = {}): IncidentSummary {
  return {
    id: "inc-1",
    state: "open",
    severity: "critical",
    title: "Checkout errors spiking",
    primary_reason: "error_rate",
    opened_at: "2026-08-10T00:00:00Z",
    last_critical_at: "2026-08-10T00:00:00Z",
    acknowledged_at: null,
    resolved_at: null,
    version: 1,
    project_key: "alpha",
    environment_key: "production",
    service_key: "checkout",
    environment_service_id: "es-1",
    binding: null as never,
    current_health: null,
    ...overrides,
  };
}

function alert(overrides: Partial<AlertInstance> = {}): AlertInstance {
  return {
    id: "alert-1",
    fingerprint_prefix: "abc123",
    alert_name: "HighErrorRate",
    status: "firing",
    severity: "critical",
    priority: "P1",
    mapping_state: "mapped",
    mapping_error_code: null,
    owner_team: null,
    slo_key: null,
    runbook_key: null,
    starts_at: "2026-08-10T01:00:00Z",
    ends_at: null,
    last_seen_at: "2026-08-10T01:00:00Z",
    source_event_at: "2026-08-10T01:00:00Z",
    ingested_at: "2026-08-10T01:00:00Z",
    resolved_at: null,
    labels: {},
    annotations: {},
    occurrence: 1,
    silenced: false,
    inhibited: false,
    namespace: null,
    version: 1,
    project_key: "alpha",
    environment_key: "production",
    service_key: "checkout",
    cluster_ref: null,
    incident: null,
    ...overrides,
  };
}

function deployment(overrides: Partial<DeploymentRow> = {}): DeploymentRow {
  return {
    id: "dep-1",
    namespace: "alpha-production",
    workload_kind: "Deployment",
    workload_name: "checkout",
    revision: 4,
    observed_generation: 4,
    images: [],
    primary_image: null,
    primary_digest: null,
    short_digest: "a1b2c3d",
    commit_sha: null,
    short_commit: "f00d",
    workflow: { provider: null, repository: null, run_id: null, run_url: null },
    evidence_state: "verified",
    evidence_detail: {},
    rollout_state: "healthy",
    rollout_reason: null,
    replicas: { desired: 3, ready: 3, updated: 3, available: 3 },
    rollout_started_at: "2026-08-10T02:00:00Z",
    rollout_completed_at: "2026-08-10T02:05:00Z",
    last_seen_at: "2026-08-10T02:05:00Z",
    cluster: { cluster_ref: "prod-1", id: "c1" },
    project_key: "alpha",
    environment_key: "production",
    service_key: "checkout",
    environment_service_id: "es-1",
    binding_id: null,
    previous_revision_id: null,
    health_comparison: null,
    ...overrides,
  };
}

describe("incidentEvents", () => {
  it("always emits an opened event, critical toned", () => {
    const events = incidentEvents([incident()]);
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ kind: "incident_opened", tone: "critical" });
  });

  it("emits a second, success-toned event only when resolved", () => {
    const events = incidentEvents([incident({ resolved_at: "2026-08-11T00:00:00Z" })]);
    expect(events).toHaveLength(2);
    expect(events[1]).toMatchObject({ kind: "incident_resolved", tone: "success" });
  });
});

describe("alertEvents", () => {
  it("maps severity to tone for the firing event", () => {
    const [critical] = alertEvents([alert({ severity: "critical" })]);
    expect(critical.tone).toBe("critical");
    const [medium] = alertEvents([alert({ id: "a2", severity: "medium" })]);
    expect(medium.tone).toBe("warning");
    const [info] = alertEvents([alert({ id: "a3", severity: "info" })]);
    expect(info.tone).toBe("info");
  });

  it("emits a resolved event only when the alert has resolved", () => {
    expect(alertEvents([alert()])).toHaveLength(1);
    expect(alertEvents([alert({ resolved_at: "2026-08-10T03:00:00Z" })])).toHaveLength(2);
  });
});

describe("deploymentEvents", () => {
  it("maps rollout state to tone", () => {
    const [healthy] = deploymentEvents([deployment({ rollout_state: "healthy" })]);
    expect(healthy.tone).toBe("success");
    const [failed] = deploymentEvents([deployment({ id: "d2", rollout_state: "failed" })]);
    expect(failed.tone).toBe("critical");
    const [unknown] = deploymentEvents([deployment({ id: "d3", rollout_state: "unknown" })]);
    expect(unknown.tone).toBe("unknown");
  });

  it("labels with commit when present, falling back to the digest", () => {
    const [withCommit] = deploymentEvents([deployment({ short_commit: "f00d" })]);
    expect(withCommit.label).toContain("f00d");
    const [withDigest] = deploymentEvents([
      deployment({ id: "d2", short_commit: null, short_digest: "a1b2c3d" }),
    ]);
    expect(withDigest.label).toContain("a1b2c3d");
  });
});

describe("buildTimeline", () => {
  it("always emits the service/cluster health lane with no fabricated history", () => {
    const lanes = buildTimeline(
      [incident()],
      [alert()],
      [deployment()],
    );
    const healthLane = lanes.find((lane) => lane.key === "cluster-service-health");
    expect(healthLane).toBeDefined();
    expect(healthLane?.historyAvailable).toBe(false);
    expect(healthLane?.events).toHaveLength(0);
    // Even though every other lane has a live event this run.
    expect(lanes.filter((lane) => lane.historyAvailable)).toHaveLength(3);
  });

  it("sorts each lane's events ascending by time", () => {
    const lanes = buildTimeline(
      [
        incident({ id: "later", opened_at: "2026-08-12T00:00:00Z" }),
        incident({ id: "earlier", opened_at: "2026-08-01T00:00:00Z" }),
      ],
      [],
      [],
    );
    const incidentsLane = lanes.find((lane) => lane.key === "incidents");
    expect(incidentsLane?.events.map((event) => event.id)).toEqual([
      "incident-opened:earlier",
      "incident-opened:later",
    ]);
  });
});

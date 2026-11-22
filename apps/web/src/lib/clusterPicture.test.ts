import { describe, expect, it } from "vitest";

import { clusterPicture } from "./clusterPicture";
import type { InfraSnapshot } from "./infrastructure";

const GiB = 1024 ** 3;

function node(name: string, cpu: number | null, cores: number | null, health = "healthy") {
  return {
    name,
    kubelet_version: null,
    health,
    cores,
    cpu: { now: cpu, series: [], allocatable: cores, requested: null },
    memory: { now: 0.5, series: [], allocatable: 16 * GiB, requested: null },
    disk: { now: 0.25, series: [], size_bytes: 100 * GiB, growth_per_day: null },
    load5: { now: null, series: [] },
    pods: { running: 10, allocatable: 110 },
  };
}

function ns(namespace: string, nodeName: string, cpu: number, project: string | null, env = "test") {
  return {
    namespace,
    node: nodeName,
    group: project
      ? { kind: "project" as const, key: project, name: project }
      : { kind: "kubernetes" as const, key: "kubernetes", name: "kubernetes" },
    project: project
      ? { project_id: `p-${project}`, project_key: project, project_name: project, environment_id: `e-${project}-${env}`, environment_key: env }
      : null,
    cpu: { now: cpu, series: [] },
    memory: { now: GiB, series: [] },
    cpu_requested: null,
    memory_requested: null,
    cpu_limit: null,
    memory_limit: null,
    pods: 2,
    restarts_24h: 0,
  };
}

const SNAP = {
  cluster: { id: "c1", ref: "duosis-prod-1", name: "Duosis Production" },
  state: "ok",
  built_at: "",
  times: [],
  step_seconds: 300,
  nodes: [node("node1", 0.5, 8), node("node2", 0.25, 8, "degraded")],
  namespaces: [
    ns("hermes-test", "node1", 0.4, "hermes"),
    ns("hermes-test", "node2", 0.2, "hermes"),
    ns("hermes-dev", "node1", 0.1, "hermes", "dev"),
    ns("kube-system", "node1", 0.3, null),
  ],
  missing: [],
} satisfies InfraSnapshot;

describe("clusterPicture", () => {
  it("sums use against capacity across the machines", () => {
    const p = clusterPicture(SNAP);
    expect(p.nodes).toEqual({ total: 2, healthy: 1 });
    expect(p.cpu.used).toBeCloseTo(6); // 0.5×8 + 0.25×8
    expect(p.cpu.capacity).toBe(16);
    expect(p.cpu.ratio).toBeCloseTo(0.375);
    expect(p.disk.ratio).toBeCloseTo(0.25);
    expect(p.pods).toMatchObject({ used: 20, capacity: 220 });
  });

  it("merges a namespace that runs on two machines, and groups the apps", () => {
    const p = clusterPicture(SNAP);
    expect(p.namespaces).toBe(3);
    expect(p.apps).toHaveLength(1);
    const hermes = p.apps[0];
    expect(hermes.envs.map((e) => [e.key, e.cpu])).toEqual([
      ["test", expect.closeTo(0.6)],
      ["dev", 0.1],
    ]);
    expect(p.groups.map((g) => g.key)).toEqual(["hermes", "kubernetes"]);
  });

  it("leaves a resource nobody reported unknown, never zero", () => {
    const p = clusterPicture({ ...SNAP, nodes: [node("node1", null, null)] });
    expect(p.cpu).toEqual({ used: null, capacity: null, ratio: null });
  });
});

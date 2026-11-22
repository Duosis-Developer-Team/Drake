/**
 * One cluster, summed up from its infrastructure snapshot: how much of the
 * machines is in use, and which apps run on it.
 *
 * Every total is a sum of what the snapshot reported. A resource no node
 * reported stays null (shown as a dash), never zero, and a ratio is only
 * drawn when both the use and the capacity behind it were reported.
 */

import type { InfraSnapshot, NamespaceSnapshot } from "@/lib/infrastructure";

export interface Usage {
  used: number | null;
  capacity: number | null;
  ratio: number | null;
}

function usage(parts: { used: number | null; capacity: number | null }[]): Usage {
  const both = parts.filter((p) => p.used !== null && p.capacity !== null && p.capacity > 0);
  if (both.length === 0) return { used: null, capacity: null, ratio: null };
  const used = both.reduce((a, p) => a + (p.used as number), 0);
  const capacity = both.reduce((a, p) => a + (p.capacity as number), 0);
  return { used, capacity, ratio: capacity ? used / capacity : null };
}

export interface ClusterApp {
  projectId: string;
  projectKey: string;
  name: string;
  envs: {
    environmentId: string;
    key: string;
    namespace: string;
    cpu: number | null;
    memory: number | null;
    pods: number | null;
  }[];
  cpu: number;
  memory: number;
  pods: number;
}

export interface NamespaceGroupUse {
  kind: NamespaceSnapshot["group"]["kind"];
  key: string;
  name: string;
  projectId: string | null;
  namespaces: { namespace: string; nodes: string[]; cpu: number | null; memory: number | null; pods: number | null; restarts: number | null; memoryLimit: number | null; cpuRequested: number | null }[];
  cpu: number;
  memory: number;
  pods: number;
}

export interface ClusterPicture {
  nodes: { total: number; healthy: number };
  /** CPU in cores. */
  cpu: Usage;
  /** Memory in bytes. */
  memory: Usage;
  /** Disk in bytes. */
  disk: Usage;
  pods: Usage;
  namespaces: number;
  apps: ClusterApp[];
  groups: NamespaceGroupUse[];
}

const add = (a: number | null, b: number | null) => (a === null ? b : b === null ? a : a + b);

export function clusterPicture(snap: InfraSnapshot): ClusterPicture {
  const nodes = snap.nodes;
  const cpu = usage(
    nodes.map((n) => ({
      used: n.cpu.now !== null && n.cores ? n.cpu.now * n.cores : null,
      capacity: n.cores ?? null,
    })),
  );
  const memory = usage(
    nodes.map((n) => ({
      used: n.memory.now !== null && n.memory.allocatable ? n.memory.now * n.memory.allocatable : null,
      capacity: n.memory.allocatable,
    })),
  );
  const disk = usage(
    nodes.map((n) => ({
      used: n.disk.now !== null && n.disk.size_bytes ? n.disk.now * n.disk.size_bytes : null,
      capacity: n.disk.size_bytes,
    })),
  );
  const pods = usage(nodes.map((n) => ({ used: n.pods.running, capacity: n.pods.allocatable })));

  // A namespace appears once per node it runs on; merge those rows.
  const merged = new Map<string, NamespaceGroupUse["namespaces"][number] & { group: NamespaceSnapshot["group"]; project: NamespaceSnapshot["project"] }>();
  for (const row of snap.namespaces) {
    const m = merged.get(row.namespace);
    if (!m) {
      merged.set(row.namespace, {
        namespace: row.namespace,
        nodes: [row.node],
        cpu: row.cpu.now,
        memory: row.memory.now,
        pods: row.pods,
        restarts: row.restarts_24h,
        memoryLimit: row.memory_limit,
        cpuRequested: row.cpu_requested,
        group: row.group,
        project: row.project,
      });
      continue;
    }
    m.nodes.push(row.node);
    m.cpu = add(m.cpu, row.cpu.now);
    m.memory = add(m.memory, row.memory.now);
    m.pods = add(m.pods, row.pods);
    m.restarts = add(m.restarts, row.restarts_24h);
    m.memoryLimit = add(m.memoryLimit, row.memory_limit);
    m.cpuRequested = add(m.cpuRequested, row.cpu_requested);
  }
  // A namespace with nothing running and nothing used is history, not a tenant.
  const live = [...merged.values()].filter(
    (n) => (n.pods ?? 0) > 0 || (n.cpu ?? 0) > 0.0005 || (n.memory ?? 0) > 1024 * 1024,
  );

  const groups = new Map<string, NamespaceGroupUse>();
  const apps = new Map<string, ClusterApp>();
  for (const n of live) {
    const g =
      groups.get(n.group.key) ??
      ({ kind: n.group.kind, key: n.group.key, name: n.group.name, projectId: null, namespaces: [], cpu: 0, memory: 0, pods: 0 } as NamespaceGroupUse);
    g.namespaces.push(n);
    g.cpu += n.cpu ?? 0;
    g.memory += n.memory ?? 0;
    g.pods += n.pods ?? 0;
    if (!g.projectId && n.project) g.projectId = n.project.project_id;
    groups.set(n.group.key, g);

    if (n.project) {
      const a =
        apps.get(n.project.project_id) ??
        ({ projectId: n.project.project_id, projectKey: n.project.project_key, name: n.project.project_name, envs: [], cpu: 0, memory: 0, pods: 0 } as ClusterApp);
      a.envs.push({
        environmentId: n.project.environment_id,
        key: n.project.environment_key,
        namespace: n.namespace,
        cpu: n.cpu,
        memory: n.memory,
        pods: n.pods,
      });
      a.cpu += n.cpu ?? 0;
      a.memory += n.memory ?? 0;
      a.pods += n.pods ?? 0;
      apps.set(a.projectId, a);
    }
  }

  return {
    nodes: { total: nodes.length, healthy: nodes.filter((n) => n.health === "healthy").length },
    cpu,
    memory,
    disk,
    pods,
    namespaces: live.length,
    apps: [...apps.values()].sort((a, b) => b.cpu - a.cpu || a.name.localeCompare(b.name)),
    groups: [...groups.values()].sort((a, b) => b.cpu - a.cpu),
  };
}

"use client";

/**
 * Access Control: roles, scoped grants, and the audit trail.
 * The API enforces every decision; this page only shapes what it requests.
 * No fake identities, roles, or audit rows — everything shown is server data.
 */

import { useEffect, useState } from "react";

import { AuditPanel } from "@/components/admin/AuditPanel";
import { GrantsPanel } from "@/components/admin/GrantsPanel";
import { RolesPanel } from "@/components/admin/RolesPanel";
import { DataState } from "@/components/state/DataState";
import { Card } from "@/components/ui/Card";
import { useSession } from "@/lib/session";
import { PageFrame } from "@/components/shell/AppShell";

type Tab = "roles" | "grants" | "audit";

export default function AccessControlPage() {
  const { state, hasPermission } = useSession();
  const canManage = hasPermission("rbac.manage");
  const canAudit = hasPermission("audit.view");
  const [tab, setTab] = useState<Tab>("roles");

  // Session data arrives asynchronously; keep the active tab within the
  // caller's visible set once permissions are known.
  useEffect(() => {
    const visible: Tab[] = [
      ...(canManage ? (["roles", "grants"] as Tab[]) : []),
      ...(canAudit ? (["audit"] as Tab[]) : []),
    ];
    if (visible.length > 0 && !visible.includes(tab)) setTab(visible[0]);
  }, [canManage, canAudit, tab]);

  if (state.status !== "authenticated") return null;

  if (!canManage && !canAudit) {
    return (
      <div className="mx-auto max-w-3xl">
        <Card title="Access Control">
          <DataState
            kind="permission-denied"
            description="Managing access requires rbac.manage; reading the audit trail requires audit.view."
          />
        </Card>
      </div>
    );
  }

  const tabs: { key: Tab; label: string; visible: boolean }[] = [
    { key: "roles", label: "Roles", visible: canManage },
    { key: "grants", label: "Grants", visible: canManage },
    { key: "audit", label: "Audit", visible: canAudit },
  ];

  return (
    <PageFrame>
      <div className="space-y-5">
      <div>
        <h1 className="text-title font-semibold text-ink">Access Control</h1>
        <p className="mt-1 max-w-3xl text-caption text-ink-secondary">
          Dynamic roles, scoped grants, and the append-only audit trail.
        </p>
      </div>

      <div role="tablist" aria-label="Access control sections" className="flex gap-1 border-b border-border">
        {tabs
          .filter((entry) => entry.visible)
          .map((entry) => (
            <button
              key={entry.key}
              role="tab"
              aria-selected={tab === entry.key}
              onClick={() => setTab(entry.key)}
              className={`-mb-px rounded-t-lg border-x border-t px-4 py-2 text-sm font-medium focus-visible:outline-2 focus-visible:outline-accent ${
                tab === entry.key
                  ? "border-border bg-surface text-ink"
                  : "border-transparent text-ink-muted hover:text-ink"
              }`}
            >
              {entry.label}
            </button>
          ))}
      </div>

      {tab === "roles" && canManage ? <RolesPanel /> : null}
      {tab === "grants" && canManage ? <GrantsPanel /> : null}
      {tab === "audit" && canAudit ? <AuditPanel /> : null}
      </div>
    </PageFrame>
  );
}

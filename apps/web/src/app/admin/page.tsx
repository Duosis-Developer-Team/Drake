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
import { Panel } from "@/components/ui/Panel";
import { DeniedState } from "@/components/ui/states";
import { useSession } from "@/lib/session";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

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
      <PageFrame width="narrow">
        <PageHeader
          title="Audit & access"
          description="Dynamic roles, scoped grants, and the append-only audit trail."
        />
        <Panel>
          <DeniedState description="Managing access requires rbac.manage; reading the audit trail requires audit.view." />
        </Panel>
      </PageFrame>
    );
  }

  const tabs: { key: Tab; label: string; visible: boolean }[] = [
    { key: "roles", label: "Roles", visible: canManage },
    { key: "grants", label: "Grants", visible: canManage },
    { key: "audit", label: "Audit", visible: canAudit },
  ];

  return (
    <PageFrame>
      <PageHeader
        title="Audit & access"
        description="Dynamic roles, scoped grants, and the append-only audit trail."
      />
      <div className="space-y-4">
      <div role="tablist" aria-label="Access control sections" className="flex gap-1 border-b border-border">
        {tabs
          .filter((entry) => entry.visible)
          .map((entry) => (
            <button
              key={entry.key}
              role="tab"
              aria-selected={tab === entry.key}
              onClick={() => setTab(entry.key)}
              className={`-mb-px border-b-2 px-3 py-2 text-body font-medium transition-colors ${
                tab === entry.key
                  ? "border-brand text-ink"
                  : "border-transparent text-ink-secondary hover:text-ink"
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

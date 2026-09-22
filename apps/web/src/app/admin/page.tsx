"use client";

/**
 * Access Control: roles, scoped grants, and the audit trail.
 * The API enforces every decision; this page only shapes what it requests.
 * No fake identities, roles, or audit rows — everything shown is server data.
 */

import { useEffect, useState } from "react";
import { KeyRound, ScrollText, ShieldCheck } from "lucide-react";

import { AuditPanel } from "@/components/admin/AuditPanel";
import { GrantsPanel } from "@/components/admin/GrantsPanel";
import { RolesPanel } from "@/components/admin/RolesPanel";
import { PillTabs, StateCard } from "@/components/features/configure/kit";
import { Panel } from "@/components/ui/Panel";
import { useT } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

type Tab = "roles" | "grants" | "audit";

export default function AccessControlPage() {
  const t = useT("admin");
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
        <PageHeader title={t("page.title")} description={t("page.description")} />
        <Panel>
          <StateCard
            kind="permission-denied"
            icon={ShieldCheck}
            description={t("page.denied")}
          />
        </Panel>
      </PageFrame>
    );
  }

  const tabs = [
    { key: "roles" as Tab, label: t("tabs.roles"), icon: ShieldCheck, visible: canManage },
    { key: "grants" as Tab, label: t("tabs.grants"), icon: KeyRound, visible: canManage },
    { key: "audit" as Tab, label: t("tabs.audit"), icon: ScrollText, visible: canAudit },
  ].filter((entry) => entry.visible);

  return (
    <PageFrame>
      <PageHeader
        title={t("page.title")}
        description={t("page.description")}
        tabs={<PillTabs label={t("page.sections")} value={tab} tabs={tabs} onChange={setTab} />}
      />
      {tab === "roles" && canManage ? <RolesPanel /> : null}
      {tab === "grants" && canManage ? <GrantsPanel /> : null}
      {tab === "audit" && canAudit ? <AuditPanel /> : null}
    </PageFrame>
  );
}

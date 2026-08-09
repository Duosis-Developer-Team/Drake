"use client";

/**
 * Onboarding session detail: discovery, plan, approval, result.
 *
 * The plan is the point of this screen. It says, item by item, what
 * applying the repository's stated intent would do to the catalog —
 * separated into what would be created, what would be linked to something
 * that already exists, what would change nothing, and what Drake cannot
 * resolve and refuses to guess at.
 *
 * Apply is live only when the SERVER says the plan is applicable and the
 * caller holds the apply right. Both are re-checked on the request; the
 * button state is a convenience, never a boundary.
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { useRef, useState } from "react";

import { LoadGate, MetaRow, useApi } from "@/components/catalog/primitives";
import { ActionBadge, GitOpsBadge, SessionBadge } from "@/components/onboarding/primitives";
import { SessionActions } from "@/components/onboarding/SessionActions";
import { DataState } from "@/components/state/DataState";
import { Card } from "@/components/ui/Card";
import {
  formatAge,
  shortSha,
  type Analysis,
  type ApplyResult,
  type Finding,
  type GitHubStatus,
  type OnboardingSession,
  type Plan,
  type PlanItem,
} from "@/lib/onboarding";
import { useSession } from "@/lib/session";

/**
 * The five outcomes a plan item can have, in the order an operator reads
 * them: what appears, what attaches to something that exists, what CHANGES
 * on a row that already exists, what stays as it is, and what Drake will not
 * decide.
 *
 * `update_metadata` used to sit under "No change", which was the one
 * grouping that could mislead: an item that rewrites a display name is not
 * a no-op, and filing it under one hides the only part of an apply that
 * edits an existing row.
 */
const GROUPS: { title: string; actions: string[]; note?: string }[] = [
  { title: "Would create", actions: ["create"] },
  { title: "Would link to an existing catalog row", actions: ["link"] },
  {
    title: "Would update metadata",
    actions: ["update_metadata"],
    note: "These rewrite fields on rows that already exist. Approving accepts these exact values.",
  },
  { title: "No change", actions: ["no_change"] },
  {
    title: "Needs a decision",
    actions: ["conflict", "unmapped", "unsupported"],
    note: "Apply is blocked until each of these is resolved. Drake refuses to choose rather than filing something under the wrong project.",
  },
];

/** Before and after, side by side. Never a raw JSON blob. */
function Changes({ item }: { item: PlanItem }) {
  const fields = Object.entries(item.changes ?? {});
  if (fields.length === 0) return null;
  return (
    <dl className="mt-1 ml-6 space-y-1" data-testid={`changes-${item.item_key}`}>
      {fields.map(([field, pair]) => (
        <div key={field} className="text-[11px]">
          <dt className="font-mono text-ink-secondary">{field}</dt>
          <dd className="ml-3 flex flex-wrap gap-x-3">
            <span className="text-ink-muted">
              before:{" "}
              <span className="font-mono text-ink-secondary">{renderValue(pair.before)}</span>
            </span>
            <span className="text-ink-muted">
              after:{" "}
              <span className="font-mono text-ink">{renderValue(pair.after)}</span>
            </span>
          </dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * An absent value is shown as absent.
 *
 * `null` here means the field had nothing recorded, which is not the same
 * as an empty string and definitely not the same as zero. Rendering it as
 * `""` would make "there was no display name" look like "the display name
 * was blank".
 */
function renderValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value === "" ? "—" : value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  // Objects and arrays are summarised, never dumped: a plan review is not a
  // place to read serialized JSON.
  return Array.isArray(value) ? `${value.length} item(s)` : "(structured value)";
}

export default function OnboardingSessionPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const { state: auth } = useSession();
  const csrfToken = auth.status === "authenticated" ? auth.me.csrf_token : "";
  const [session, reloadSession] = useApi<OnboardingSession>(
    `/v1/onboarding/sessions/${sessionId}`,
  );
  const [plan, reloadPlan] = useApi<{ plan: Plan | null; items: PlanItem[] }>(
    `/v1/onboarding/sessions/${sessionId}/plan`,
  );
  const [findings, reloadFindings] = useApi<{ analysis: Analysis | null; findings: Finding[] }>(
    `/v1/onboarding/sessions/${sessionId}/findings`,
  );
  const [status] = useApi<GitHubStatus>("/v1/onboarding/github/status");

  // Owned by the screen, not by the action panel. Reloading the session
  // unmounts that panel, so a result kept inside it would disappear the
  // instant the apply that produced it refreshed the page — and the
  // idempotency key would be regenerated, turning the next retry into a
  // second operation.
  const [applyResult, setApplyResult] = useState<ApplyResult | null>(null);
  const applyKey = useRef<{ version: number; key: string } | null>(null);

  // One refresh for all three. A mutation can change the session's state,
  // its plan and its findings together, and reloading only the one the
  // button belongs to leaves the other two describing a session that has
  // moved on.
  const reloadAll = () => {
    reloadSession();
    reloadPlan();
    reloadFindings();
  };

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <LoadGate value={session} retry={reloadSession}>
        {(data) => (
          <>
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <h1 className="text-xl font-semibold text-ink">
                  {data.repository.full_name}
                </h1>
                <p className="mt-1 font-mono text-xs break-all text-ink-muted">
                  {data.repository.default_branch} ·{" "}
                  {shortSha(data.analyzed_commit_sha)}
                </p>
              </div>
              <SessionBadge state={data.state} />
            </div>

            {data.repository.security_gate ? (
              <Card title="Security gate">
                <div data-testid="security-gate">
                  <DataState
                    kind="permission-denied"
                    title="Closed by a manual security gate"
                    description="This repository cannot be onboarded until an operator closes the gate. Drake makes no provider call and issues no token for it."
                  />
                </div>
              </Card>
            ) : null}

            {data.state === "stale" ? (
              <Card title="Out of date">
                <div data-testid="stale-notice">
                  <DataState
                    kind="stale"
                    title="The repository moved"
                    description="This plan describes a commit that is no longer the branch head. A review of a commit is not a review of its successor, so it cannot be applied. Analyse again."
                  />
                </div>
              </Card>
            ) : null}

            <SessionActions
              session={data}
              plan={plan.state === "ready" ? plan.data.plan : null}
              csrfToken={csrfToken}
              gitopsEnabled={status.state === "ready" ? status.data.gitops_pr_enabled : false}
              onChanged={reloadAll}
              result={applyResult}
              onResult={setApplyResult}
              applyKey={applyKey}
            />

            <div className="grid gap-5 md:grid-cols-2">
              <Card title="Safe discovery">
                {findings.state === "loading" ? (
                  <DataState kind="loading" />
                ) : findings.state === "error" ? (
                  <DataState kind="error" description={findings.message} />
                ) : findings.data.analysis === null ? (
                  <DataState
                    kind="empty"
                    title="Not analysed yet"
                    description="No repository has been read for this session."
                  />
                ) : (
                  <div className="space-y-1" data-testid="analysis">
                    <MetaRow label="Commit">
                      {shortSha(findings.data.analysis.commit_sha)}
                    </MetaRow>
                    <MetaRow label="Files read">
                      {String(findings.data.analysis.files_read)}
                    </MetaRow>
                    <MetaRow label="Manifest">
                      {findings.data.analysis.manifest_found ? "found" : "absent"}
                    </MetaRow>
                    <MetaRow label="Analysed">
                      {formatAge(findings.data.analysis.analyzed_at)}
                    </MetaRow>
                    {findings.data.analysis.truncated ? (
                      <div data-testid="analysis-truncated">
                        <DataState
                          kind="partial"
                          title="Partial analysis"
                          description="The analysis stopped at a budget, so this describes part of the repository. It is not a complete picture."
                        />
                      </div>
                    ) : null}
                    <p className="mt-2 text-xs text-ink-muted">
                      Paths and digests only. Drake stores no file content, and never reads
                      environment files, private keys, credentials or cluster configuration.
                    </p>
                  </div>
                )}
              </Card>

              <Card title="Session">
                <MetaRow label="State">{data.state}</MetaRow>
                <MetaRow label="Plan version">
                  {data.plan ? `v${data.plan.plan_version}` : "—"}
                </MetaRow>
                <MetaRow label="Approved">
                  {data.approved_at
                    ? `v${data.approved_plan_version} · ${formatAge(data.approved_at)}`
                    : "not approved"}
                </MetaRow>
                <MetaRow label="Imported">
                  {data.imported_project_key ? (
                    <Link
                      href={`/projects/${data.imported_project_id}`}
                      className="hover:underline"
                    >
                      {data.imported_project_key}
                    </Link>
                  ) : (
                    "not imported"
                  )}
                </MetaRow>
                {data.reason ? (
                  <p className="mt-2 text-xs text-ink-secondary">{data.reason}</p>
                ) : null}
              </Card>
            </div>

            <Card title="Proposed changes">
              {plan.state === "loading" ? (
                <DataState kind="loading" />
              ) : plan.state === "error" ? (
                <DataState kind="error" description={plan.message} />
              ) : plan.data.plan === null ? (
                <DataState
                  kind="empty"
                  title="No plan yet"
                  description="Analyse the repository to produce a proposal."
                />
              ) : (
                <div className="space-y-4" data-testid="plan">
                  {/*
                    `min-w-0` and `break-all` on the digest: a 64-character
                    monospace token is one unbreakable word, so flex-wrap
                    cannot help it — it pushed the page 19px wider than the
                    viewport at 768px, which scrolls every row's right-hand
                    end (where the actions are) off screen.
                  */}
                  <div className="flex min-w-0 flex-wrap gap-4 text-xs text-ink-secondary">
                    <span>
                      Plan <span className="font-mono">v{plan.data.plan.plan_version}</span>
                    </span>
                    <span>
                      Commit{" "}
                      <span className="font-mono">
                        {shortSha(plan.data.plan.commit_sha)}
                      </span>
                    </span>
                    <span>
                      Digest{" "}
                      <span className="font-mono break-all">
                        {plan.data.plan.plan_digest}
                      </span>
                    </span>
                    <span>{plan.data.plan.total_items} items</span>
                  </div>

                  {GROUPS.map((group) => {
                    const items = plan.data.items.filter((item) =>
                      group.actions.includes(item.action),
                    );
                    if (items.length === 0) return null;
                    return (
                      <div key={group.title} data-testid={`plan-group-${group.actions[0]}`}>
                        <p className="mb-1.5 text-xs font-medium text-ink">{group.title}</p>
                        <ul className="space-y-1.5">
                          {items.map((item) => (
                            <li
                              key={item.item_key}
                              className="flex min-w-0 flex-wrap items-baseline gap-2 break-words"
                            >
                              <ActionBadge action={item.action} />
                              <span className="font-mono text-xs text-ink">
                                {item.entity_kind}
                              </span>
                              <span className="text-xs text-ink-secondary">
                                {item.proposed_name ?? item.existing_name ?? item.item_key}
                              </span>
                              {item.reason ? (
                                <span className="text-[11px] text-ink-muted">
                                  {item.reason}
                                </span>
                              ) : null}
                              {item.entity_kind === "deployment_source" &&
                              item.detail?.materialized === false ? (
                                <span
                                  className="text-[11px] text-ink-muted"
                                  data-testid="deployment-source-note"
                                >
                                  Recorded as evidence only — no catalog row is written for it.
                                </span>
                              ) : null}
                              <Changes item={item} />
                            </li>
                          ))}
                        </ul>
                        {group.note ? (
                          <p className="mt-1.5 text-[11px] text-ink-muted">{group.note}</p>
                        ) : null}
                      </div>
                    );
                  })}

                  <div className="border-t border-border pt-3">
                    {plan.data.plan.applicable && data.can_apply ? (
                      <p className="text-xs text-ink-secondary" data-testid="apply-available">
                        This plan can be applied. Applying writes to Drake&apos;s catalog and
                        changes nothing in the repository.
                      </p>
                    ) : (
                      <p className="text-xs text-warning" data-testid="apply-blocked">
                        {plan.data.plan.blocking_items > 0
                          ? `Apply is blocked: ${plan.data.plan.blocking_items} item(s) need a decision.`
                          : data.can_apply === false
                            ? "You can review this plan but not apply it. Applying needs the onboarding apply permission."
                            : "This plan is not currently applicable."}
                      </p>
                    )}
                  </div>
                </div>
              )}
            </Card>

            {data.gitops_requests && data.gitops_requests.length > 0 ? (
              <Card title="Manifest pull requests">
                <ul className="space-y-2" data-testid="gitops-requests">
                  {data.gitops_requests.map((entry) => (
                    <li key={entry.id} className="flex min-w-0 flex-wrap items-baseline gap-2">
                      <GitOpsBadge state={entry.state} />
                      <span className="font-mono text-xs break-all text-ink-secondary">
                        {entry.branch_name} → {entry.file_path}
                      </span>
                      {entry.pull_request_url ? (
                        <a
                          href={entry.pull_request_url}
                          target="_blank"
                          // `noopener` so the opened tab cannot reach back
                          // through `window.opener`; `noreferrer` so Drake's
                          // URL — which contains a session id — is not sent
                          // to GitHub as a referrer.
                          rel="noopener noreferrer"
                          data-testid={`gitops-pr-link-${entry.id}`}
                          className="text-xs text-ink hover:underline"
                        >
                          Open draft pull request #{entry.provider_pr_number} →
                        </a>
                      ) : null}
                      {entry.error_code ? (
                        <span className="font-mono text-[11px] break-all text-critical">
                          {entry.error_code}
                        </span>
                      ) : null}
                    </li>
                  ))}
                </ul>
                <p className="mt-3 text-xs text-ink-muted">
                  The pull request Drake opens is a <strong>draft</strong>, and deliberately
                  incomplete: every <span className="font-mono">REPLACE_ME</span> in it is a
                  decision a person has to make. Fill them in and merge it in GitHub.
                </p>
                <p className="mt-1.5 text-xs text-ink-muted">
                  Merging it does not import anything into Drake — it puts the manifest in the
                  repository, which is where Drake reads intent from. Analyse again afterwards
                  and approve the plan; the import happens there.
                </p>
              </Card>
            ) : null}
          </>
        )}
      </LoadGate>
    </div>
  );
}

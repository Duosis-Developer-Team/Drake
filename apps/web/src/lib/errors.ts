/**
 * Error tracking (ADR-0036): what went wrong in an environment, grouped
 * into issues. Read from the store each time; nothing is stored, so
 * "first seen" means first seen in the chosen window.
 */

import { apiGet } from "@/lib/api";

export type IssueKind = "exception" | "failed_request" | "error_log";
export const ISSUE_KINDS: IssueKind[] = ["exception", "failed_request", "error_log"];
export type ErrorRange = "6h" | "24h";

export interface Issue {
  id: string;
  kind: IssueKind;
  binding_id: string;
  title: string;
  count: number;
  first_seen: string;
  last_seen: string;
  trend: number[];
  trace_id: string | null;
  message: string | null;
  exception_type?: string;
  stacktrace?: string | null;
  routes?: string[];
  route?: string;
  code?: string;
  severity_number?: number;
}

export interface ErrorSection {
  state: "ok" | "unavailable" | "not_configured";
  reason?: string;
}

export interface ErrorsRead {
  range: ErrorRange;
  as_of: string;
  bucket_seconds: number | null;
  buckets: number[];
  sections: Record<IssueKind, ErrorSection>;
  issues: Issue[];
  summary: {
    issues: Record<IssueKind, number>;
    events: Record<IssueKind, number>;
    total_issues: number;
  };
}

export function errorsPath(bindingIds: string[], range: ErrorRange): string | null {
  if (!bindingIds.length) return null;
  const query = new URLSearchParams({ range });
  for (const id of [...bindingIds].sort()) query.append("binding_id", id);
  return `/v1/service-health/errors?${query}`;
}

export function fetchErrors(path: string): Promise<ErrorsRead> {
  return apiGet<ErrorsRead>(path);
}

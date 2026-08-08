/**
 * Typed notification surface: inbox, policies, destinations, delivery audit.
 *
 * The browser composes no message text and decides no routing. It renders
 * what the server already wrote, and the only things it can send are ids
 * and enum values — there is no field in this file for a URL, a header, a
 * token or a message body, because there is none on the server either.
 */

import { apiGet, apiMutate } from "@/lib/api";

export type NotificationEventType = "opened" | "acknowledged" | "auto_resolved";
export type DestinationType = "in_app_user" | "webhook";
export type DeliveryState =
  | "pending"
  | "processing"
  | "retrying"
  | "delivered"
  | "dead_letter"
  | "suppressed";

export const EVENT_TYPES: NotificationEventType[] = [
  "opened",
  "acknowledged",
  "auto_resolved",
];

export const EVENT_TYPE_LABELS: Record<NotificationEventType, string> = {
  opened: "Incident opened",
  acknowledged: "Incident acknowledged",
  auto_resolved: "Incident resolved",
};

export const DELIVERY_STATE_LABELS: Record<DeliveryState, string> = {
  pending: "Pending",
  processing: "Sending",
  retrying: "Retrying",
  delivered: "Delivered",
  dead_letter: "Dead letter",
  suppressed: "Suppressed",
};

/** Bounded classification codes the API may return. Anything unmapped is
 * shown verbatim — the codes are already a fixed server vocabulary. */
export const DELIVERY_ERROR_LABELS: Record<string, string> = {
  timeout: "Receiver timed out",
  connect_failed: "Could not connect",
  transport_error: "Transport error",
  destination_not_configured: "Destination no longer configured",
  destination_redirect_refused: "Receiver redirected (not followed)",
  destination_private_refused: "Target resolved to a private address",
  destination_target_refused: "Target address refused",
  destination_mixed_answers_refused: "Target resolved inconsistently",
  destination_unresolvable: "Target could not be resolved",
};

export interface InboxItem {
  id: string;
  event_type: NotificationEventType | null;
  title: string;
  body: string;
  target_path: string | null;
  metadata: Record<string, string>;
  created_at: string;
  read_at: string | null;
  incident_id: string | null;
  /** False when the reader's access to the incident has since been removed. */
  accessible: boolean;
}

export interface InboxPage {
  items: InboxItem[];
  next_cursor: string | null;
  limit: number;
}

export interface NotificationPolicy {
  id: string;
  display_name: string;
  project_id: string;
  project_key: string;
  environment_id: string | null;
  environment_key: string | null;
  service_id: string | null;
  service_key: string | null;
  event_types: NotificationEventType[];
  severities: string[];
  enabled: boolean;
  version: number;
  destination_count: number;
}

export interface PolicyDetail extends NotificationPolicy {
  destinations: {
    id: string;
    destination_type: DestinationType;
    display_name: string;
    destination_key: string | null;
    enabled: boolean;
  }[];
}

export interface NotificationDestination {
  id: string;
  destination_type: DestinationType;
  display_name: string;
  /** An opaque handle into the operator's registry. The URL it resolves to
   * is not exposed by any endpoint. */
  destination_key: string | null;
  enabled: boolean;
  project_id: string;
  recipient: { display_name: string; email: string } | null;
  payload_schema_version: number;
  version: number;
}

export interface PolicyOptions {
  event_types: NotificationEventType[];
  severities: string[];
  destination_types: DestinationType[];
  webhook_keys: { key: string; display_name: string; payload_schema_version: number }[];
}

export interface DeliveryRow {
  id: string;
  state: DeliveryState;
  attempt_count: number;
  next_attempt_at: string | null;
  delivered_at: string | null;
  last_error_code: string | null;
  last_http_status: number | null;
  created_at: string;
  destination_display_name: string;
  event_type: NotificationEventType;
  incident_id: string;
  incident_title: string;
  project_key: string;
}

export interface DeliveryAttempt {
  attempt_number: number;
  started_at: string;
  completed_at: string | null;
  outcome: "delivered" | "retryable" | "terminal" | "refused";
  http_status: number | null;
  error_code: string | null;
  duration_ms: number | null;
  retry_at: string | null;
}

export async function fetchInbox(
  options: { unreadOnly?: boolean; cursor?: string } = {},
): Promise<InboxPage> {
  const query = new URLSearchParams();
  if (options.unreadOnly) query.set("unread_only", "true");
  if (options.cursor) query.set("cursor", options.cursor);
  const suffix = query.toString() ? `?${query}` : "";
  return apiGet<InboxPage>(`/v1/notifications${suffix}`);
}

export async function fetchUnreadCount(): Promise<number> {
  const body = await apiGet<{ unread: number }>("/v1/notifications/unread-count");
  return body.unread;
}

export async function markRead(
  csrfToken: string,
  notificationIds: string[],
): Promise<number> {
  const body = await apiMutate<{ marked_read: number }>("/v1/notifications/read", {
    csrfToken,
    body: { notification_ids: notificationIds },
  });
  return body.marked_read;
}

export async function fetchPolicies(projectId?: string): Promise<NotificationPolicy[]> {
  const suffix = projectId ? `?project_id=${projectId}` : "";
  const body = await apiGet<{ policies: NotificationPolicy[] }>(
    `/v1/notification-policies${suffix}`,
  );
  return body.policies;
}

export async function fetchPolicyOptions(): Promise<PolicyOptions> {
  return apiGet<PolicyOptions>("/v1/notification-policies/options");
}

export async function fetchDestinations(
  projectId?: string,
): Promise<NotificationDestination[]> {
  const suffix = projectId ? `?project_id=${projectId}` : "";
  const body = await apiGet<{ destinations: NotificationDestination[] }>(
    `/v1/notification-destinations${suffix}`,
  );
  return body.destinations;
}

export async function createPolicy(
  csrfToken: string,
  body: {
    display_name: string;
    project_id: string;
    environment_id?: string | null;
    service_id?: string | null;
    event_types: NotificationEventType[];
  },
): Promise<{ id: string; version: number }> {
  return apiMutate("/v1/notification-policies", { csrfToken, body });
}

export async function updatePolicy(
  csrfToken: string,
  policyId: string,
  body: {
    display_name: string;
    environment_id?: string | null;
    service_id?: string | null;
    event_types: NotificationEventType[];
    enabled: boolean;
    expected_version: number;
  },
): Promise<{ id: string; version: number }> {
  return apiMutate(`/v1/notification-policies/${policyId}`, { csrfToken, body });
}

export async function fetchDeliveries(state?: DeliveryState): Promise<DeliveryRow[]> {
  const suffix = state ? `?state=${state}` : "";
  const body = await apiGet<{ items: DeliveryRow[] }>(`/v1/notification-deliveries${suffix}`);
  return body.items;
}

export async function fetchDeliveryAttempts(deliveryId: string): Promise<DeliveryAttempt[]> {
  const body = await apiGet<{ attempts: DeliveryAttempt[] }>(
    `/v1/notification-deliveries/${deliveryId}/attempts`,
  );
  return body.attempts;
}

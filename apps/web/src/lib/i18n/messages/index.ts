/**
 * The catalogue, one namespace per area.
 *
 * Adding an area: create `messages/<area>.ts` with `defineMessages`, import
 * it here, and add it to `MESSAGES`. Nothing else changes; `useT("<area>")`
 * is typed from this object.
 */
import { admin } from "./admin";
import { agents } from "./agents";
import { alertRules } from "./alertRules";
import { alerting } from "./alerting";
import { catalog } from "./catalog";
import { clusters } from "./clusters";
import { commandCenter } from "./commandCenter";
import { dashboards } from "./dashboards";
import { common } from "./common";
import { deployments } from "./deployments";
import { explore } from "./explore";
import { incidents } from "./incidents";
import { infrastructure } from "./infrastructure";
import { integrations } from "./integrations";
import { logs } from "./logs";
import { notifications } from "./notifications";
import { onboarding } from "./onboarding";
import { protection } from "./protection";
import { scheduledJobs } from "./scheduledJobs";
import { serviceHealth } from "./serviceHealth";
import { serviceMap } from "./serviceMap";
import { shell } from "./shell";
import { signals } from "./signals";
import { traces } from "./traces";
import { ui } from "./ui";

export const MESSAGES = {
  common,
  shell,
  commandCenter,
  dashboards,
  catalog,
  clusters,
  incidents,
  infrastructure,
  alerting,
  alertRules,
  deployments,
  explore,
  serviceHealth,
  serviceMap,
  agents,
  scheduledJobs,
  logs,
  traces,
  signals,
  protection,
  notifications,
  integrations,
  onboarding,
  admin,
  ui,
} as const;

export type Messages = typeof MESSAGES;
export type NamespaceName = keyof Messages;

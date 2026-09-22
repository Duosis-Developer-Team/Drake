/**
 * The catalogue, one namespace per area.
 *
 * Adding an area: create `messages/<area>.ts` with `defineMessages`, import
 * it here, and add it to `MESSAGES`. Nothing else changes; `useT("<area>")`
 * is typed from this object.
 */
import { admin } from "./admin";
import { alerting } from "./alerting";
import { catalog } from "./catalog";
import { clusters } from "./clusters";
import { commandCenter } from "./commandCenter";
import { common } from "./common";
import { deployments } from "./deployments";
import { incidents } from "./incidents";
import { integrations } from "./integrations";
import { notifications } from "./notifications";
import { onboarding } from "./onboarding";
import { protection } from "./protection";
import { serviceHealth } from "./serviceHealth";
import { shell } from "./shell";
import { ui } from "./ui";

export const MESSAGES = {
  common,
  shell,
  commandCenter,
  catalog,
  clusters,
  incidents,
  alerting,
  deployments,
  serviceHealth,
  protection,
  notifications,
  integrations,
  onboarding,
  admin,
  ui,
} as const;

export type Messages = typeof MESSAGES;
export type NamespaceName = keyof Messages;

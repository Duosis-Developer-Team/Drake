/**
 * The badge moved to `components/ui` with the rest of the primitives, and its
 * colour vocabulary moved to `lib/design/status`. This module keeps the old
 * import path working so the screens can migrate one at a time.
 */
export {
  HealthIndicator,
  HealthWord,
  StatusBadge,
  StatusDot,
  type HealthStatus,
  type StatusTone,
} from "@/components/ui/StatusBadge";

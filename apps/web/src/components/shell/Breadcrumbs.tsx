"use client";

/**
 * Breadcrumbs derived from the route.
 *
 * The previous version printed a fixed "Organization / <section>" plus a
 * "detail" chip, which said the same thing on every screen and named nothing.
 * This one walks the real path segments and renders every ancestor that is a
 * real, reachable route.
 *
 * Identifier segments — the `{projectId}` in `/projects/p1/...` — are shown as
 * the identifier, monospaced, and NOT resolved to a display name here: this
 * component has no data, and fetching one name per crumb on every navigation
 * would be four extra requests to render a header. The page itself puts the
 * display name in its own title, which is where a reader looks for it.
 */

import { ChevronRight } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { NAV_ITEMS } from "@/lib/navigation";

/** Static segments that name a concept rather than an entity. */
const SEGMENT_LABELS: Record<string, string> = {
  admin: "Audit & access",
  alerts: "Alerts",
  bind: "New binding",
  clusters: "Clusters",
  deployments: "Deployments",
  environments: "Environments",
  github: "GitHub",
  incidents: "Incidents",
  integrations: "Integrations",
  inventory: "Inventory",
  "notification-deliveries": "Deliveries",
  "notification-policies": "Notification routing",
  notifications: "Notifications",
  onboarding: "Onboarding",
  projects: "Projects",
  protection: "Protection",
  "service-health": "Service health",
  services: "Services",
  slo: "Objectives",
};

/** Routes that exist as pages; anything else is rendered as plain text. */
const LINKABLE = new Set<string>([
  ...NAV_ITEMS.map((item) => item.href),
  "/notifications",
  "/notification-deliveries",
  "/integrations/github",
  "/service-health/bind",
]);

export interface Crumb {
  label: string;
  href: string | null;
  /** Entity identifiers are monospaced so they read as values, not words. */
  mono: boolean;
}

export function buildCrumbs(pathname: string): Crumb[] {
  const segments = pathname.split("/").filter(Boolean);
  if (segments.length === 0) return [{ label: "Command Center", href: null, mono: false }];

  const crumbs: Crumb[] = [];
  let path = "";
  for (const segment of segments) {
    path += `/${segment}`;
    const known = SEGMENT_LABELS[segment];
    crumbs.push({
      label: known ?? decodeURIComponent(segment),
      href: LINKABLE.has(path) ? path : null,
      mono: !known,
    });
  }
  // The last crumb is the current page and is never a link.
  crumbs[crumbs.length - 1].href = null;
  return crumbs;
}

export function Breadcrumbs() {
  const pathname = usePathname() || "/";
  const crumbs = buildCrumbs(pathname);

  return (
    <nav aria-label="Breadcrumb" className="min-w-0">
      <ol className="flex min-w-0 items-center gap-1 text-caption">
        {crumbs.map((crumb, index) => {
          const last = index === crumbs.length - 1;
          return (
            <li key={`${crumb.label}-${index}`} className="flex min-w-0 items-center gap-1">
              {index > 0 ? (
                <ChevronRight aria-hidden className="h-3.5 w-3.5 shrink-0 text-ink-muted" />
              ) : null}
              {crumb.href ? (
                <Link
                  href={crumb.href}
                  className={`truncate rounded text-ink-secondary hover:text-ink ${
                    crumb.mono ? "font-mono text-micro" : ""
                  }`}
                >
                  {crumb.label}
                </Link>
              ) : (
                <span
                  aria-current={last ? "page" : undefined}
                  className={`truncate ${last ? "font-medium text-ink" : "text-ink-secondary"} ${
                    crumb.mono ? "font-mono text-micro" : ""
                  }`}
                >
                  {crumb.label}
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

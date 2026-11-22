"use client";

/**
 * Breadcrumbs derived from the route.
 *
 * The previous version printed a fixed "Organization / <section>" plus a
 * "detail" chip, which said the same thing on every screen and named nothing.
 * This one walks the real path segments and renders every ancestor that is a
 * real, reachable route.
 *
 * Identifier segments — the `{projectId}` in `/projects/p1/...` — show
 * whatever name the page has published for them through `lib/crumbs`, and the
 * raw identifier until it does. This component fetches nothing: one request
 * per crumb on every navigation would be four extra round trips to render a
 * header, and they would land after the page already had.
 */

import { ArrowLeft, ChevronRight } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { useCrumbLabels } from "@/lib/crumbs";
import { useT, type Translator } from "@/lib/i18n";
import { NAV_ITEMS } from "@/lib/navigation";

/**
 * Static segments that name a concept rather than an entity. The English
 * source; the rendered label comes from `shell.crumb.<segment>`.
 */
const SEGMENT_LABELS: Record<string, string> = {
  admin: "Audit & access",
  alerts: "Incoming alerts",
  "alert-rules": "Alert rules",
  bind: "New binding",
  clusters: "Clusters",
  dashboards: "Dashboards",
  deployments: "Deployments",
  environments: "Environments",
  github: "GitHub",
  incidents: "Incidents",
  infrastructure: "Resource usage",
  integrations: "Integrations",
  inventory: "Inventory",
  logs: "Logs",
  "notification-deliveries": "Deliveries",
  "notification-policies": "Notification routing",
  notifications: "Notifications",
  onboarding: "Onboarding",
  projects: "Projects",
  protection: "Protection",
  "service-health": "Services",
  services: "Services",
  slo: "Objectives",
  traces: "Traces",
};

/** Routes that exist as pages. Static ones by path; entity pages by shape. */
const LINKABLE = new Set<string>([
  ...NAV_ITEMS.map((item) => item.href),
  "/notifications",
  "/notification-deliveries",
  "/integrations/github",
  "/service-health/bind",
]);

/** Every `[id]` route in `app/`, as the shape of its path. */
const ENTITY_ROUTES: RegExp[] = [
  /^\/agents\/[^/]+$/,
  /^\/agents\/[^/]+\/runs\/[^/]+$/,
  /^\/alerts\/[^/]+$/,
  /^\/clusters\/[^/]+$/,
  /^\/clusters\/[^/]+\/inventory$/,
  /^\/clusters\/[^/]+\/inventory\/[^/]+$/,
  /^\/dashboards\/[^/]+$/,
  /^\/deployments\/[^/]+$/,
  /^\/incidents\/[^/]+$/,
  /^\/onboarding\/[^/]+$/,
  /^\/projects\/[^/]+$/,
  /^\/projects\/[^/]+\/environments\/[^/]+$/,
  /^\/projects\/[^/]+\/environments\/[^/]+\/services\/[^/]+$/,
  /^\/protection\/[^/]+$/,
  /^\/service-health\/[^/]+$/,
  /^\/slo\/[^/]+$/,
];

export function isPage(path: string): boolean {
  return LINKABLE.has(path) || ENTITY_ROUTES.some((route) => route.test(path));
}

export interface Crumb {
  label: string;
  href: string | null;
  /** Entity identifiers are monospaced so they read as values, not words. */
  mono: boolean;
}

export function buildCrumbs(
  pathname: string,
  labelFor: (id: string) => string | undefined = () => undefined,
  t?: Translator<"shell">,
): Crumb[] {
  const segments = pathname.split("/").filter(Boolean);
  if (segments.length === 0) {
    return [{ label: t ? t("breadcrumb.commandCenter") : "Command Center", href: null, mono: false }];
  }

  const crumbs: Crumb[] = [];
  let path = "";
  // The nearest ancestor that is a page: a grouping segment with no page of
  // its own ("Environments" under a project) opens the page that lists it.
  let lastPage: string | null = null;
  for (const segment of segments) {
    path += `/${segment}`;
    const source = SEGMENT_LABELS[segment];
    const known = source === undefined ? undefined : t ? t.dyn("crumb", segment, source) : source;
    const published = known ? undefined : labelFor(decodeURIComponent(segment));
    const page = isPage(path);
    if (page) lastPage = path;
    crumbs.push({
      label: known ?? published ?? decodeURIComponent(segment),
      href: page ? path : lastPage,
      // An identifier reads as a value; a name the page published reads as a
      // word, and takes the same type as the rest of the trail.
      mono: !known && !published,
    });
  }
  // The last crumb is the current page and is never a link.
  crumbs[crumbs.length - 1].href = null;
  return crumbs;
}

/**
 * How many pages this tab has shown inside the app. Back goes back only when
 * there is somewhere in the app to go back to; a page opened from a link
 * goes up to its parent instead of leaving Tracesis.
 */
let visited = 0;

function BackButton({ parent }: { parent: string | null }) {
  const t = useT("shell");
  const router = useRouter();
  const pathname = usePathname() || "/";
  const [inApp, setInApp] = useState(false);
  useEffect(() => {
    visited += 1;
    setInApp(visited > 1);
  }, [pathname]);
  if (!inApp && !parent) return null;
  return (
    <button
      type="button"
      onClick={() => (inApp ? router.back() : parent && router.push(parent))}
      aria-label={t("breadcrumb.back")}
      title={t("breadcrumb.back")}
      data-testid="nav-back"
      className="mr-1 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-border bg-surface text-ink-secondary transition-colors hover:bg-surface-hover hover:text-ink"
    >
      <ArrowLeft aria-hidden className="h-3.5 w-3.5" />
    </button>
  );
}

export function Breadcrumbs() {
  const pathname = usePathname() || "/";
  const labelFor = useCrumbLabels();
  const t = useT("shell");
  const crumbs = buildCrumbs(pathname, labelFor, t);
  const parent = crumbs.length > 1 ? crumbs[crumbs.length - 2].href : null;

  return (
    <nav aria-label={t("breadcrumb.label")} className="flex min-w-0 items-center">
      <BackButton parent={parent} />
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

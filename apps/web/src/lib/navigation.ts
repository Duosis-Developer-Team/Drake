/**
 * Primary navigation (Tracery information architecture, 2026-11-21).
 *
 * Grouped by the question an operator is asking, in the order they ask it:
 *
 *   (top)          what is happening — overview, services, projects, boards
 *   Respond        what needs action — incidents, rules, incoming alerts,
 *                  what changed
 *   Infrastructure what it runs on — clusters, resource usage, objectives,
 *                  backups
 *   Settings       how Tracery is set up — onboarding, integrations,
 *                  notification routing, access
 *
 * Every entry points at a route that exists; no module was dropped in the
 * regrouping. `anyPermission` shapes the menu; it is not a security
 * boundary. The API remains the authority, and hiding a link never stands in
 * for an authorization check.
 */
import {
  BellRing,
  Cpu,
  FolderKanban,
  House,
  Inbox,
  Layers,
  PackagePlus,
  PanelsTopLeft,
  Puzzle,
  Rocket,
  Send,
  Server,
  Shield,
  ShieldCheck,
  Siren,
  Target,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  /** Catalogue key under `shell.nav`; `label` is the English source string. */
  key: string;
  label: string;
  href: string;
  icon: LucideIcon;
  /** Any one of these unlocks the entry. Absent means always visible. */
  anyPermission?: string[];
  /** Longest-prefix matching for detail routes underneath this entry. */
  matchPrefix?: string;
}

export interface NavGroup {
  key: string;
  label: string;
  /** The first group sits under the logo without a heading. */
  untitled?: boolean;
  items: NavItem[];
}

export const NAVIGATION: NavGroup[] = [
  {
    key: "overview",
    label: "Overview",
    untitled: true,
    items: [
      { key: "commandCenter", label: "Overview", href: "/", icon: House },
      {
        key: "serviceHealth",
        label: "Services",
        href: "/service-health",
        icon: Layers,
        matchPrefix: "/service-health",
        anyPermission: ["environment.view"],
      },
      {
        key: "projects",
        label: "Projects",
        href: "/projects",
        icon: FolderKanban,
        matchPrefix: "/projects",
        anyPermission: ["project.view", "environment.view"],
      },
      {
        key: "dashboards",
        label: "Dashboards",
        href: "/dashboards",
        icon: PanelsTopLeft,
        matchPrefix: "/dashboards",
        anyPermission: ["environment.view"],
      },
    ],
  },
  {
    key: "operations",
    label: "Respond",
    items: [
      {
        key: "incidents",
        label: "Incidents",
        href: "/incidents",
        icon: Siren,
        matchPrefix: "/incidents",
        anyPermission: ["environment.view"],
      },
      {
        key: "alertRules",
        label: "Alert rules",
        href: "/alert-rules",
        icon: BellRing,
        matchPrefix: "/alert-rules",
        anyPermission: ["alert.view"],
      },
      {
        key: "alerts",
        label: "Incoming alerts",
        href: "/alerts",
        icon: Inbox,
        matchPrefix: "/alerts",
        anyPermission: ["alert.view"],
      },
      {
        key: "deployments",
        label: "Deployments",
        href: "/deployments",
        icon: Rocket,
        matchPrefix: "/deployments",
        anyPermission: ["environment.view", "cluster.view"],
      },
    ],
  },
  {
    key: "estate",
    label: "Infrastructure",
    items: [
      {
        key: "clusters",
        label: "Clusters",
        href: "/clusters",
        icon: Server,
        matchPrefix: "/clusters",
        anyPermission: ["cluster.view"],
      },
      {
        key: "infrastructure",
        label: "Resource usage",
        href: "/infrastructure",
        icon: Cpu,
        matchPrefix: "/infrastructure",
        anyPermission: ["cluster.view"],
      },
      {
        key: "slo",
        label: "Objectives",
        href: "/slo",
        icon: Target,
        matchPrefix: "/slo",
        anyPermission: ["slo.view"],
      },
      {
        key: "protection",
        label: "Protection",
        href: "/protection",
        icon: ShieldCheck,
        matchPrefix: "/protection",
        anyPermission: ["protection.view"],
      },
    ],
  },
  {
    key: "configuration",
    label: "Settings",
    items: [
      {
        key: "onboarding",
        label: "Onboard project",
        href: "/onboarding",
        icon: PackagePlus,
        matchPrefix: "/onboarding",
        anyPermission: ["onboarding.view", "onboarding.manage"],
      },
      {
        key: "integrations",
        label: "Integrations",
        href: "/integrations",
        icon: Puzzle,
        matchPrefix: "/integrations",
        anyPermission: ["project.view", "environment.view", "cluster.view"],
      },
      {
        key: "notificationPolicies",
        label: "Notification routing",
        href: "/notification-policies",
        icon: Send,
        matchPrefix: "/notification-",
        anyPermission: ["notification.view", "notification.manage"],
      },
      {
        key: "admin",
        label: "Audit & access",
        href: "/admin",
        icon: Shield,
        matchPrefix: "/admin",
        anyPermission: ["rbac.manage", "audit.view"],
      },
    ],
  },
];

export const NAV_ITEMS: NavItem[] = NAVIGATION.flatMap((group) => group.items);

/**
 * Which entry a path belongs to.
 *
 * Longest prefix wins so `/projects/p1/environments/e1` highlights Projects,
 * while `/` only ever matches Overview exactly — a prefix match on "/"
 * would light up every entry at once.
 */
export function activeNavHref(pathname: string): string | null {
  let best: { href: string; length: number } | null = null;
  for (const item of NAV_ITEMS) {
    if (item.href === "/") {
      if (pathname === "/") return "/";
      continue;
    }
    const prefix = item.matchPrefix ?? item.href;
    if (pathname === prefix || pathname.startsWith(`${prefix}/`) || pathname.startsWith(prefix)) {
      if (!best || prefix.length > best.length) best = { href: item.href, length: prefix.length };
    }
  }
  return best?.href ?? null;
}

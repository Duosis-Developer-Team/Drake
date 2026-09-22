/**
 * Primary navigation.
 *
 * Grouped by what an operator is doing, not by which sprint shipped the
 * screen: Overview is where you start, Estate is what you look at (the
 * platform's own inventory), Operations is where you act on what estate
 * surfaces, and Configuration is governance and setup.
 *
 * Every entry points at a route that exists. The "coming in a later sprint"
 * placeholders that used to sit here — Tenants, Catalog & Templates — are
 * gone: a permanently disabled menu item teaches people the nav is unreliable
 * and does nothing else.
 *
 * `anyPermission` shapes the menu; it is not a security boundary. The API
 * remains the authority, and hiding a link never stands in for an
 * authorization check.
 */
import {
  Bell,
  Boxes,
  FolderKanban,
  Gauge,
  HeartPulse,
  LayoutDashboard,
  PackagePlus,
  Puzzle,
  Rocket,
  Send,
  Shield,
  ShieldCheck,
  Siren,
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
  items: NavItem[];
}

export const NAVIGATION: NavGroup[] = [
  {
    key: "overview",
    label: "Overview",
    items: [{ key: "commandCenter", label: "Command Center", href: "/", icon: LayoutDashboard }],
  },
  {
    key: "estate",
    label: "Estate",
    items: [
      {
        key: "projects",
        label: "Projects",
        href: "/projects",
        icon: FolderKanban,
        matchPrefix: "/projects",
        anyPermission: ["project.view", "environment.view"],
      },
      {
        key: "serviceHealth",
        label: "Service health",
        href: "/service-health",
        icon: HeartPulse,
        matchPrefix: "/service-health",
        anyPermission: ["environment.view"],
      },
      {
        key: "clusters",
        label: "Clusters",
        href: "/clusters",
        icon: Boxes,
        matchPrefix: "/clusters",
        anyPermission: ["cluster.view"],
      },
      {
        key: "slo",
        label: "Objectives",
        href: "/slo",
        icon: Gauge,
        matchPrefix: "/slo",
        anyPermission: ["slo.view"],
      },
    ],
  },
  {
    key: "operations",
    label: "Operations",
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
        key: "alerts",
        label: "Alerts",
        href: "/alerts",
        icon: Bell,
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
    label: "Configuration",
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
 * while `/` only ever matches Command Center exactly — a prefix match on "/"
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

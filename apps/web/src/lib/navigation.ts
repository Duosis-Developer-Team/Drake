/**
 * Primary navigation, mirroring the product information architecture.
 * Routes beyond the Command Center are placeholders until their sprints land.
 */
import {
  Activity,
  Bell,
  Boxes,
  FolderKanban,
  LayoutDashboard,
  Puzzle,
  Rocket,
  Shield,
  ShieldCheck,
  Users,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  label: string;
  href: string;
  icon: LucideIcon;
  /** True when the destination screen exists in the current sprint. */
  enabled: boolean;
  /**
   * Any of these permissions unlocks the entry. UI gating is a convenience —
   * the API remains the authorization authority.
   */
  anyPermission?: string[];
}

export const NAVIGATION: NavItem[] = [
  { label: "Command Center", href: "/", icon: LayoutDashboard, enabled: true },
  { label: "Projects", href: "/projects", icon: FolderKanban, enabled: false },
  { label: "Clusters", href: "/clusters", icon: Boxes, enabled: false },
  { label: "Tenants", href: "/tenants", icon: Users, enabled: false },
  { label: "Alerts & Incidents", href: "/incidents", icon: Bell, enabled: false },
  { label: "Protection", href: "/protection", icon: ShieldCheck, enabled: false },
  { label: "Deployments", href: "/deployments", icon: Rocket, enabled: false },
  { label: "Integrations", href: "/integrations", icon: Puzzle, enabled: false },
  { label: "Catalog & Templates", href: "/catalog", icon: Activity, enabled: false },
  {
    label: "Audit & Administration",
    href: "/admin",
    icon: Shield,
    enabled: true,
    anyPermission: ["rbac.manage", "audit.view"],
  },
];

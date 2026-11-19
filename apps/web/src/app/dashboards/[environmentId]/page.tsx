"use client";

import { useParams } from "next/navigation";

import { DashboardScreen } from "@/components/features/dashboards/DashboardScreen";

export default function EnvironmentDashboardPage() {
  const { environmentId } = useParams<{ environmentId: string }>();
  return <DashboardScreen environmentId={environmentId} />;
}

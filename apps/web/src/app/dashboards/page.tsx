"use client";

import { Suspense } from "react";

import { DashboardIndex } from "@/components/features/dashboards/DashboardIndex";
import { DataState } from "@/components/state/DataState";

export default function DashboardsPage() {
  return (
    <Suspense fallback={<DataState kind="loading" />}>
      <DashboardIndex />
    </Suspense>
  );
}

"use client";

import { Suspense } from "react";

import { ExploreScreen } from "@/components/explore/ExploreScreen";
import { DataState } from "@/components/state/DataState";

export default function LogsPage() {
  return (
    <Suspense fallback={<DataState kind="loading" />}>
      <ExploreScreen kind="logs" />
    </Suspense>
  );
}

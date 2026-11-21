"use client";

import { Suspense } from "react";

import { ServiceExplorer } from "@/components/explore/ServiceExplorer";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { DataState } from "@/components/state/DataState";
import { useT } from "@/lib/i18n";

export default function TracesPage() {
  const t = useT("explore");
  return (
    <PageFrame>
      <PageHeader title={t("traces.title")} description={t("traces.description")} />
      <Suspense fallback={<DataState kind="loading" />}>
        <ServiceExplorer kind="traces" />
      </Suspense>
    </PageFrame>
  );
}

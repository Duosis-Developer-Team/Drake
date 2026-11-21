"use client";

import { Suspense } from "react";

import { ServiceExplorer } from "@/components/explore/ServiceExplorer";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { DataState } from "@/components/state/DataState";
import { useT } from "@/lib/i18n";

export default function LogsPage() {
  const t = useT("explore");
  return (
    <PageFrame>
      <PageHeader title={t("logs.title")} description={t("logs.description")} />
      <Suspense fallback={<DataState kind="loading" />}>
        <ServiceExplorer kind="logs" />
      </Suspense>
    </PageFrame>
  );
}

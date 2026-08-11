"use client";

/**
 * Binding screen. Creates a binding for a service, or edits an existing
 * one when `binding_id` is present.
 */

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { PageFrame } from "@/components/shell/AppShell";

import { LoadGate, useApi } from "@/components/catalog/primitives";
import { BindingForm } from "@/components/service-health/BindingForm";
import { DataState } from "@/components/state/DataState";
import type { BindingSummary } from "@/lib/serviceHealth";

/** The binding endpoint's payload, reshaped into the summary the form takes. */
interface BindingDetail {
  id: string;
  namespace: string;
  workload_kind: string;
  workload_name: string;
  resolution: { resolved: boolean; resource_uid: string | null; resolved_at: string | null };
  preset_key: string;
  health_policy_key: string;
  lifecycle: string;
  revision: number;
  cluster: { cluster_ref: string; id: string };
  project_key: string;
  environment_key: string;
  service_key: string;
  environment_service_id: string;
}

function toSummary(detail: BindingDetail): BindingSummary {
  return {
    id: detail.id,
    lifecycle: detail.lifecycle,
    resolved: detail.resolution.resolved,
    resolved_at: detail.resolution.resolved_at,
    revision: detail.revision,
    namespace: detail.namespace,
    workload_kind: detail.workload_kind,
    workload_name: detail.workload_name,
    cluster_ref: detail.cluster.cluster_ref,
    cluster_id: detail.cluster.id,
    preset_key: detail.preset_key,
    health_policy_key: detail.health_policy_key,
    project_key: detail.project_key,
    environment_key: detail.environment_key,
    service_key: detail.service_key,
    environment_service_id: detail.environment_service_id,
    datasource_configured: false,
  };
}

function BindScreen() {
  const params = useSearchParams();
  const router = useRouter();
  const environmentServiceId = params.get("environment_service_id") ?? "";
  const bindingId = params.get("binding_id");
  const [existing, retry] = useApi<BindingDetail>(
    bindingId ? `/v1/service-health/bindings/${bindingId}` : null,
  );

  if (!environmentServiceId) {
    return (
      <DataState
        kind="not-configured"
        title="No service selected"
        description="Open this screen from a service in the health list."
      />
    );
  }

  if (!bindingId) {
    return (
      <BindingForm
        environmentServiceId={environmentServiceId}
        onSaved={(id) => router.push(`/service-health/${id}`)}
      />
    );
  }

  return (
    <LoadGate value={existing} retry={retry}>
      {(detail) => (
        <BindingForm
          environmentServiceId={environmentServiceId}
          existing={toSummary(detail)}
          onSaved={() => retry()}
        />
      )}
    </LoadGate>
  );
}

export default function BindPage() {
  return (
    <PageFrame>
      <div className="space-y-5">
      <div>
        <p className="text-xs text-ink-muted">
          <Link href="/service-health" className="hover:text-ink">
            Service health
          </Link>{" "}
          / Binding
        </p>
        <h1 className="mt-1 text-title font-semibold text-ink">
          Service ↔ workload binding
        </h1>
        <p className="mt-1 max-w-3xl text-caption text-ink-secondary">
          Choose the workload this service runs as. Which metrics are read comes from a
          reviewed preset — there is no query to write here.
        </p>
      </div>
      <Suspense fallback={<DataState kind="loading" />}>
        <BindScreen />
      </Suspense>
      </div>
    </PageFrame>
  );
}

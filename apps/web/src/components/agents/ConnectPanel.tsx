"use client";

/**
 * How to make an agent appear: the real endpoints of this deployment, as
 * environment variables to paste. No token is ever shown — the external
 * door's token is a Secret an administrator hands out.
 */

import { Check, Copy, Globe, Radar, Server, ShieldCheck } from "lucide-react";
import { useState } from "react";

import type { AgentConnect } from "@/lib/agents";
import { useT } from "@/lib/i18n";

function Snippet({ text }: { text: string }) {
  const t = useT("agents");
  const [copied, setCopied] = useState(false);
  return (
    <div className="group relative">
      <pre className="whitespace-pre-wrap break-all rounded-control border border-border bg-surface-2 px-3.5 py-3 pr-20 font-mono text-micro leading-relaxed text-ink">
        {text}
      </pre>
      <button
        type="button"
        onClick={() => {
          void navigator.clipboard?.writeText(text).then(() => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
          });
        }}
        className="absolute top-2 right-2 inline-flex items-center gap-1 rounded-control border border-border bg-surface px-2 py-1 text-micro font-medium text-ink-secondary hover:text-ink"
      >
        {copied ? (
          <Check aria-hidden className="h-3 w-3" />
        ) : (
          <Copy aria-hidden className="h-3 w-3" />
        )}
        {copied ? t("connectPanel.copied") : t("connectPanel.copy")}
      </button>
    </div>
  );
}

function Way({
  icon: Icon,
  title,
  hint,
  children,
}: {
  icon: typeof Server;
  title: string;
  hint: string;
  children?: React.ReactNode;
}) {
  return (
    <section className="space-y-2.5">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-control bg-brand-soft text-brand">
          <Icon aria-hidden className="h-4 w-4" />
        </span>
        <div>
          <h3 className="text-body font-semibold text-ink">{title}</h3>
          <p className="text-caption text-ink-secondary">{hint}</p>
        </div>
      </div>
      {children ? <div className="pl-11">{children}</div> : null}
    </section>
  );
}

export function ConnectPanel({ connect }: { connect: AgentConnect | null }) {
  const t = useT("agents");
  const inside = connect
    ? `OTEL_EXPORTER_OTLP_ENDPOINT=${connect.cluster_endpoint}\nOTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf\nOTEL_SERVICE_NAME=<your-agent-service>\nOTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`
    : "";
  const outside = connect?.external_endpoint
    ? `OTEL_EXPORTER_OTLP_ENDPOINT=${connect.external_endpoint}\nOTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf\nOTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer <ingest token>\nOTEL_SERVICE_NAME=<your-agent-service>`
    : null;
  return (
    <div className="space-y-6" data-testid="connect-panel">
      <p className="text-caption text-ink-secondary">
        {t("connectPanel.lead")}
      </p>
      <Way
        icon={Server}
        title={t("connectPanel.inside")}
        hint={t("connectPanel.insideHint")}
      >
        <Snippet text={inside} />
      </Way>
      <Way
        icon={Globe}
        title={t("connectPanel.outside")}
        hint={
          outside ? t("connectPanel.outsideHint") : t("connectPanel.outsideOff")
        }
      >
        {outside ? <Snippet text={outside} /> : null}
      </Way>
      <Way
        icon={Radar}
        title={t("connectPanel.ebpf")}
        hint={t("connectPanel.ebpfHint")}
      />
      <p className="flex items-start gap-2 rounded-control bg-healthy-soft px-3.5 py-3 text-caption text-ink">
        <ShieldCheck
          aria-hidden
          className="mt-0.5 h-4 w-4 shrink-0 text-healthy"
        />
        {t("connectPanel.privacy")}
      </p>
    </div>
  );
}

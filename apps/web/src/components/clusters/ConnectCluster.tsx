"use client";

/**
 * Connect a cluster (ADR-0037).
 *
 * Two steps, one drawer. The operator names the cluster; Tracesis registers
 * it as one whose telemetry stays inside it and returns that cluster's own
 * install file and the one command that applies it. The file carries a
 * one-time token that lasts ten minutes, so it is shown once, offered as a
 * download, and never kept in the browser.
 *
 * Naming an existing cluster again (same name and display name) is how a
 * new file is made for it: registration is idempotent, and every request
 * mints a fresh token.
 */

import { Check, Copy, Download, Plug } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/controls";
import { Drawer } from "@/components/ui/overlay";
import { ApiError, apiGet, apiMutate } from "@/lib/api";
import { useFormat, useT } from "@/lib/i18n";
import { useOptionalSession } from "@/lib/session";

interface Install {
  cluster_ref: string;
  file_name: string;
  values_yaml: string;
  command: string;
  namespace: string;
  expires_at: string;
}

const REF = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;

/** A display name, turned into the DNS label a cluster ref must be. */
export function slugify(name: string): string {
  return name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/ı/g, "i")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 63)
    .replace(/-+$/g, "");
}

const KNOWN_ERRORS = [
  "agent_install_not_configured",
  "cluster_reads_central_store",
  "cluster_ref_taken",
  "invalid_cluster_ref",
] as const;

export function ConnectCluster({ onConnected }: { onConnected?: () => void }) {
  const t = useT("clusters");
  const format = useFormat();
  const session = useOptionalSession();
  const [open, setOpen] = useState(false);
  const [displayName, setDisplayName] = useState("");
  const [ref, setRef] = useState("");
  const [refTouched, setRefTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [install, setInstall] = useState<Install | null>(null);
  const [copied, setCopied] = useState(false);
  // null while asking; asked every time the drawer opens, before anything
  // is created.
  const [configured, setConfigured] = useState<boolean | null>(null);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setConfigured(null);
    apiGet<{ configured: boolean }>("/v1/agent-install", controller.signal)
      .then((status) => setConfigured(status.configured))
      .catch(() => {
        if (!controller.signal.aborted) setConfigured(false);
      });
    return () => controller.abort();
  }, [open]);

  const close = useCallback(() => {
    setOpen(false);
    setInstall(null);
    setError(null);
    setCopied(false);
  }, []);

  if (!session?.hasPermission("integration.manage")) return null;
  const csrfToken = session.state.status === "authenticated" ? session.state.me.csrf_token : "";
  const clusterRef = refTouched ? ref : slugify(displayName);
  const valid = displayName.trim().length > 0 && REF.test(clusterRef) && clusterRef.length >= 2;

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const cluster = await apiMutate<{ id: string }>("/v1/clusters", {
        csrfToken,
        body: {
          cluster_ref: clusterRef,
          display_name: displayName.trim(),
          telemetry_location: "cluster",
        },
      });
      const created = await apiMutate<Install>(`/v1/clusters/${cluster.id}/agent-install`, {
        csrfToken,
      });
      setInstall(created);
      onConnected?.();
    } catch (caught) {
      const code = caught instanceof ApiError ? caught.code : "";
      setError(
        (KNOWN_ERRORS as readonly string[]).includes(code)
          ? t.dyn("connect.error", code, t("connect.error.generic"))
          : t("connect.error.generic"),
      );
    } finally {
      setBusy(false);
    }
  };

  const download = () => {
    if (!install) return;
    const url = URL.createObjectURL(new Blob([install.values_yaml], { type: "application/yaml" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = install.file_name;
    link.click();
    URL.revokeObjectURL(url);
  };

  const copy = () => {
    if (!install) return;
    void navigator.clipboard.writeText(install.command).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <>
      <Button variant="primary" icon={Plug} onClick={() => setOpen(true)} data-testid="connect-cluster">
        {t("connect.open")}
      </Button>
      <Drawer
        open={open}
        onClose={close}
        width="lg"
        title={t("connect.title")}
        description={t("connect.description")}
        footer={
          install ? (
            <div className="flex justify-end">
              <Button onClick={close}>{t("connect.done")}</Button>
            </div>
          ) : (
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={close}>
                {t("connect.cancel")}
              </Button>
              <Button
                variant="primary"
                disabled={!valid || busy || configured !== true}
                onClick={() => void submit()}
              >
                {busy ? t("connect.working") : t("connect.submit")}
              </Button>
            </div>
          )
        }
      >
        {install ? (
          <ol className="space-y-5" data-testid="connect-install">
            <li>
              <p className="text-body font-medium text-ink">{t("connect.step.download")}</p>
              <p className="mt-1 text-caption text-ink-secondary">
                {t("connect.step.downloadHint", { time: format.utc(install.expires_at) })}
              </p>
              <Button className="mt-2" icon={Download} onClick={download}>
                {install.file_name}
              </Button>
            </li>
            <li>
              <p className="text-body font-medium text-ink">{t("connect.step.run")}</p>
              <p className="mt-1 text-caption text-ink-secondary">{t("connect.step.runHint")}</p>
              <div className="mt-2 flex items-start gap-2">
                <code
                  data-testid="connect-command"
                  className="min-w-0 flex-1 rounded-control border border-border bg-surface-2 px-3 py-2 font-mono text-caption break-all text-ink"
                >
                  {install.command}
                </code>
                <Button size="compact" icon={copied ? Check : Copy} onClick={copy}>
                  {copied ? t("connect.copied") : t("connect.copy")}
                </Button>
              </div>
            </li>
            <li>
              <p className="text-body font-medium text-ink">{t("connect.step.wait")}</p>
              <p className="mt-1 text-caption text-ink-secondary">
                {t("connect.step.waitHint", { namespace: install.namespace })}
              </p>
            </li>
            <li className="rounded-control border border-border bg-surface-2 px-3 py-2 text-caption text-ink-secondary">
              {t("connect.dataStays")}
            </li>
          </ol>
        ) : configured === false ? (
          <p data-testid="connect-unavailable" className="text-body text-ink-secondary">
            {t("connect.error.agent_install_not_configured")}
          </p>
        ) : (
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              if (valid && !busy && configured === true) void submit();
            }}
          >
            <label className="block">
              <span className="text-caption font-medium text-ink">{t("connect.field.name")}</span>
              <input
                className="mt-1 h-9 w-full rounded-control border border-border bg-surface px-3 text-body text-ink"
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                placeholder={t("connect.field.namePlaceholder")}
                maxLength={200}
                autoFocus
              />
            </label>
            <label className="block">
              <span className="text-caption font-medium text-ink">{t("connect.field.ref")}</span>
              <input
                className="mt-1 h-9 w-full rounded-control border border-border bg-surface px-3 font-mono text-body text-ink"
                value={clusterRef}
                onChange={(event) => {
                  setRefTouched(true);
                  setRef(event.target.value.toLowerCase());
                }}
                maxLength={63}
                spellCheck={false}
              />
              <span className="mt-1 block text-caption text-ink-secondary">{t("connect.field.refHint")}</span>
            </label>
            <p className="rounded-control border border-border bg-surface-2 px-3 py-2 text-caption text-ink-secondary">
              {t("connect.dataStays")}
            </p>
            {error ? (
              <p role="alert" className="text-caption text-critical">
                {error}
              </p>
            ) : null}
          </form>
        )}
      </Drawer>
    </>
  );
}

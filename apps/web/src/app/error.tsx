"use client";

import { RefreshCw } from "lucide-react";
import Link from "next/link";
import { useEffect } from "react";

import { PageFrame } from "@/components/shell/AppShell";
import { Panel } from "@/components/ui/Panel";
import { CopyableIdentifier } from "@/components/ui/identifiers";
import { useT } from "@/lib/i18n";

/**
 * The route error boundary.
 *
 * A rendering failure inside a page, not a failed request — those are handled
 * by the page itself, which can distinguish denied from empty from throttled.
 * Reaching here means something threw, and the honest thing to say is that
 * this screen is broken rather than that the platform is.
 *
 * `digest` is Next's server-side error identifier. It is the only thing that
 * connects what the user saw to a server log line, so it is copyable.
 */
export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useT("ui");
  const common = useT("common");
  useEffect(() => {
    // The browser console is where an operator looks first; the message is
    // already sanitised by Next in production builds.
    console.error("Drake route error", error);
  }, [error]);

  return (
    <PageFrame width="narrow">
      <Panel tone="critical" className="mt-6">
        <p className="text-caption font-medium text-critical">{t("error.kicker")}</p>
        <h1 className="mt-1 text-title font-semibold text-ink">{t("error.title")}</h1>
        <p className="mt-1.5 text-body text-ink-secondary">{t("error.body")}</p>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={reset}
            className="inline-flex h-9 items-center gap-1.5 rounded-control bg-brand px-3 text-body font-medium text-ink-inverse transition-colors hover:bg-brand-hover"
          >
            <RefreshCw className="h-4 w-4" aria-hidden />
            {common("action.tryAgain")}
          </button>
          <Link
            href="/"
            className="inline-flex h-9 items-center rounded-control border border-border px-3 text-body font-medium text-ink transition-colors hover:bg-surface-hover"
          >
            {t("error.commandCenter")}
          </Link>
        </div>
        {error.digest ? (
          <p className="mt-3 text-micro text-ink-muted">
            {t("error.reference")}{" "}
            <CopyableIdentifier value={error.digest} label={t("error.referenceLabel")} />
          </p>
        ) : null}
      </Panel>
    </PageFrame>
  );
}

"use client";

import Link from "next/link";

import { PageFrame } from "@/components/shell/AppShell";
import { Panel } from "@/components/ui/Panel";
import { useT } from "@/lib/i18n";

/**
 * 404.
 *
 * A route that does not exist, which is a different thing from a resource
 * that does not exist in your scope (that one is rendered by the page, as
 * `NotFoundState`, and deliberately does not distinguish itself from a denial).
 * This one is a mistyped or dead URL and says so.
 */
export default function NotFound() {
  const t = useT("ui");
  const common = useT("common");
  return (
    <PageFrame width="narrow">
      <Panel className="mt-6">
        <p className="text-caption font-medium text-ink-muted">{t("notFound.code")}</p>
        <h1 className="mt-1 text-title font-semibold text-ink">{t("notFound.title")}</h1>
        <p className="mt-1.5 text-body text-ink-secondary">{t("notFound.body")}</p>
        <p className="mt-4">
          <Link
            href="/"
            className="inline-flex h-9 items-center rounded-control bg-brand px-3 text-body font-medium text-ink-inverse transition-colors hover:bg-brand-hover"
          >
            {common("action.goHome")}
          </Link>
        </p>
      </Panel>
    </PageFrame>
  );
}

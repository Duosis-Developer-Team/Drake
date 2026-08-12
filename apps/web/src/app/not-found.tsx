import Link from "next/link";

import { PageFrame } from "@/components/shell/AppShell";
import { Panel } from "@/components/ui/Panel";

/**
 * 404.
 *
 * A route that does not exist, which is a different thing from a resource
 * that does not exist in your scope (that one is rendered by the page, as
 * `NotFoundState`, and deliberately does not distinguish itself from a denial).
 * This one is a mistyped or dead URL and says so.
 */
export default function NotFound() {
  return (
    <PageFrame width="narrow">
      <Panel className="mt-6">
        <p className="text-caption font-medium text-ink-muted">Error 404</p>
        <h1 className="mt-1 text-title font-semibold text-ink">This page does not exist</h1>
        <p className="mt-1.5 text-body text-ink-secondary">
          The address is not a Drake route. It may have been mistyped, or it may have been a
          link to something that has since been removed.
        </p>
        <p className="mt-4">
          <Link
            href="/"
            className="inline-flex h-9 items-center rounded-control bg-brand px-3 text-body font-medium text-ink-inverse transition-colors hover:bg-brand-hover"
          >
            Go to the Command Center
          </Link>
        </p>
      </Panel>
    </PageFrame>
  );
}

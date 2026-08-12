"use client";

/**
 * The signed-out surfaces.
 *
 * Three genuinely different situations, and they must not collapse into one
 * "Something went wrong" card:
 *
 *   signed-out   nobody is signed in. Offer the sign-in this deployment has.
 *   expired      somebody WAS signed in and the session ended. Say so, and
 *                come back to where they were.
 *   unavailable  the auth service cannot be reached. This is a dependency
 *                outage, not a sign-out — offering a sign-in button here
 *                sends people into a loop.
 *
 * The provider button is what renders until `/v1/auth/mode` answers. A
 * deployment in local mode swaps it for the form, and the Entra button is
 * never shown alongside it.
 */

import { LogIn } from "lucide-react";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { LocalSignIn } from "@/components/auth/LocalSignIn";
import { DrakeWordmark } from "@/components/shell/Brand";
import { ThemeControl } from "@/components/shell/ThemeControl";
import { toneSpec } from "@/lib/design/status";

const COPY = {
  "signed-out": {
    title: "Sign in to Drake",
    body: "Observability and operations control plane. Sign in with your organization account.",
    tone: "info" as const,
  },
  expired: {
    title: "Session expired",
    body: "Your session ended. Signing in again returns you to the page you were on.",
    tone: "warning" as const,
  },
  unavailable: {
    title: "Sign-in temporarily unavailable",
    body: "The authentication service cannot be reached. This is a dependency outage, not a sign-out — no action is needed beyond trying again shortly.",
    tone: "critical" as const,
  },
};

export function SignedOut({
  variant = "signed-out",
}: {
  variant?: "signed-out" | "expired" | "unavailable";
}) {
  const pathname = usePathname();
  const loginHref = `/v1/auth/login?redirect=${encodeURIComponent(pathname || "/")}`;
  const copy = COPY[variant];
  const spec = toneSpec(copy.tone);
  const Icon = spec.icon;

  const [mode, setMode] = useState<"oidc" | "local">("oidc");
  useEffect(() => {
    let cancelled = false;
    fetch("/v1/auth/mode", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : null))
      .then((body) => {
        if (!cancelled && body?.mode === "local") setMode("local");
      })
      .catch(() => {
        // Leave the provider button in place: guessing "local" here would
        // show a form no deployment answers.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div
      className="flex min-h-screen flex-col bg-canvas"
      data-testid={`screen-${variant}`}
    >
      <header className="flex h-14 items-center justify-between px-5">
        <DrakeWordmark height={22} />
        <ThemeControl compact />
      </header>

      <main className="flex flex-1 items-center justify-center px-5 pb-16">
        <div className="w-full max-w-sm rounded-panel border border-border bg-surface p-6 shadow-panel">
          <span className={`inline-flex items-center gap-2 text-caption font-medium ${spec.text}`}>
            <Icon className="h-4 w-4" aria-hidden />
            {variant === "signed-out" ? "Authentication required" : spec.label}
          </span>
          <h1 className="mt-3 text-title font-semibold text-ink">{copy.title}</h1>
          <p className="mt-1.5 text-body text-ink-secondary">{copy.body}</p>

          {variant === "unavailable" ? null : mode === "local" ? (
            <LocalSignIn />
          ) : (
            <a
              href={loginHref}
              className="mt-5 inline-flex h-10 w-full items-center justify-center gap-2 rounded-control bg-brand px-4 text-body font-medium text-ink-inverse transition-colors hover:bg-brand-hover"
            >
              <LogIn className="h-4 w-4" aria-hidden />
              Sign in
            </a>
          )}
        </div>
      </main>
    </div>
  );
}

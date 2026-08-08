"use client";

import { LogIn, ShieldCheck } from "lucide-react";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { LocalSignIn } from "@/components/auth/LocalSignIn";
import { ThemeToggle } from "@/components/shell/ThemeToggle";

/**
 * Signed-out landing. The sign-in action is a plain navigation to the API's
 * login endpoint — no tokens, no client-side OIDC, nothing in storage.
 */
export function SignedOut({
  variant = "signed-out",
}: {
  variant?: "signed-out" | "expired" | "unavailable";
}) {
  const pathname = usePathname();
  const loginHref = `/v1/auth/login?redirect=${encodeURIComponent(pathname || "/")}`;

  // Which sign-in this deployment offers. Until the answer arrives the
  // provider button is shown, which is the long-standing behaviour; a
  // deployment configured for local sign-in swaps it for the form.
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
    <div className="flex min-h-screen flex-col" data-testid={`screen-${variant}`}>
      <header className="flex h-16 items-center justify-between px-6">
        <div className="flex items-center gap-3">
          <div
            aria-hidden
            className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent font-semibold text-white"
          >
            D
          </div>
          <span className="text-sm font-semibold tracking-wide text-ink">Drake</span>
        </div>
        <ThemeToggle />
      </header>

      <main className="flex flex-1 items-center justify-center px-6">
        <div className="w-full max-w-md rounded-2xl border border-border bg-surface p-8 text-center shadow-sm">
          <div
            aria-hidden
            className="mx-auto mb-5 flex h-12 w-12 items-center justify-center rounded-xl bg-accent-soft"
          >
            <ShieldCheck className="h-6 w-6 text-accent" />
          </div>
          {variant === "unavailable" ? (
            <>
              <h1 className="text-lg font-semibold text-ink">Sign-in temporarily unavailable</h1>
              <p className="mt-2 text-sm text-ink-secondary">
                The authentication service cannot be reached right now. This is a
                dependency outage, not a sign-out. Try again shortly.
              </p>
            </>
          ) : (
            <>
              <h1 className="text-lg font-semibold text-ink">
                {variant === "expired" ? "Session expired" : "Sign in to Drake"}
              </h1>
              <p className="mt-2 text-sm text-ink-secondary">
                {variant === "expired"
                  ? "Your session ended. Sign in again to continue where you left off."
                  : "Operations control plane for your platform. Sign in with your organization account."}
              </p>
            </>
          )}
          {variant !== "unavailable" && mode === "local" ? (
            <LocalSignIn />
          ) : (
            <a
              href={loginHref}
              className="mt-6 inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-accent px-5 text-sm font-medium text-white transition-opacity hover:opacity-90 focus-visible:outline-2 focus-visible:outline-accent"
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

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

import { LogIn, ShieldCheck } from "lucide-react";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { LocalSignIn } from "@/components/auth/LocalSignIn";
import { SignInBackdrop, SignInStory } from "@/components/auth/SignInHero";
import { TracesisMark, TracesisWordmark } from "@/components/shell/Brand";
import { LanguageControl } from "@/components/shell/LanguageControl";
import { ThemeControl } from "@/components/shell/ThemeControl";
import { toneSpec } from "@/lib/design/status";
import { useT } from "@/lib/i18n";

/** Copy lives in `shell.signedOut.*`; only the tone is decided here. */
const COPY = {
  "signed-out": { key: "signedOut" as const, tone: "info" as const },
  expired: { key: "expired" as const, tone: "warning" as const },
  unavailable: { key: "unavailable" as const, tone: "critical" as const },
};

export function SignedOut({
  variant = "signed-out",
}: {
  variant?: "signed-out" | "expired" | "unavailable";
}) {
  const pathname = usePathname();
  const loginHref = `/v1/auth/login?redirect=${encodeURIComponent(pathname || "/")}`;
  const copy = COPY[variant];
  const t = useT("shell");
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
      className="relative isolate min-h-screen overflow-hidden bg-canvas"
      data-testid={`screen-${variant}`}
    >
      <SignInBackdrop />

      <header className="relative flex h-16 items-center justify-between px-6 lg:px-12">
        <TracesisWordmark size={22} />
        <span className="flex items-center gap-3">
          <LanguageControl compact />
          <ThemeControl compact />
        </span>
      </header>

      <main className="relative mx-auto grid max-w-[78rem] items-center gap-14 px-6 pt-6 pb-16 lg:min-h-[calc(100vh-4rem)] lg:grid-cols-[minmax(0,1fr)_27.5rem] lg:gap-20 lg:px-12 lg:pb-20">
        <div className="hidden lg:block">
          <SignInStory />
        </div>

        <div className="mx-auto w-full max-w-[27.5rem] rounded-[1.75rem] border border-border bg-surface/85 p-8 shadow-[0_1px_0_rgba(255,255,255,0.06)_inset,0_30px_80px_-40px_rgba(15,18,40,0.45)] backdrop-blur-xl sm:p-10">
          <TracesisMark height={26} />
          <h1 className="mt-6 font-[family-name:var(--font-display)] text-[1.75rem] leading-tight font-bold tracking-[-0.03em] text-ink">
            {variant === "signed-out"
              ? t("welcome.title")
              : t(`signedOut.${copy.key}.title`)}
          </h1>
          {variant === "signed-out" ? null : (
            <span
              className={`mt-3 inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-caption font-medium ${spec.chip}`}
            >
              <Icon className="h-3.5 w-3.5" aria-hidden />
              {spec.label}
            </span>
          )}
          <p className="mt-2 text-body text-ink-secondary">
            {variant === "signed-out"
              ? t("welcome.body")
              : t(`signedOut.${copy.key}.body`)}
          </p>

          {variant === "unavailable" ? null : (
            <div className="mt-8">
              {mode === "local" ? (
                <LocalSignIn />
              ) : (
                <a
                  href={loginHref}
                  className="inline-flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-brand px-4 text-body font-semibold text-ink-inverse transition-colors hover:bg-brand-hover"
                >
                  <LogIn className="h-4 w-4" aria-hidden />
                  {t("signedOut.signIn")}
                </a>
              )}
            </div>
          )}

          <div className="mt-8 space-y-2 border-t border-border pt-6 text-caption text-ink-muted">
            <p>{t("welcome.access")}</p>
            <p className="flex items-center gap-1.5">
              <ShieldCheck className="h-3.5 w-3.5 shrink-0" aria-hidden />
              {t("welcome.secure")}
            </p>
          </div>
        </div>
      </main>

      <footer className="relative px-6 pb-6 text-center font-mono text-[11px] tracking-wide text-ink-muted lg:absolute lg:bottom-0 lg:left-12 lg:px-0 lg:text-left">
        {t("hero.footer")}
      </footer>
    </div>
  );
}

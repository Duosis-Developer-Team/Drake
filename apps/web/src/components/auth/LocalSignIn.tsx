"use client";

import { ArrowRight, Eye, EyeOff, Loader2, Lock, Mail } from "lucide-react";
import { useId, useState } from "react";

import { useT } from "@/lib/i18n";

/**
 * Email and password sign-in, shown inside the signed-out card when the
 * deployment verifies credentials itself rather than delegating to an
 * identity provider.
 *
 * The server answers every failure identically, and so does this form: one
 * message, whatever went wrong. Saying "no such account" here would undo
 * the care taken on the server not to confirm who has access.
 */

export function LocalSignIn() {
  const emailId = useId();
  const passwordId = useId();
  const errorId = useId();
  const t = useT("shell");

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [reveal, setReveal] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (pending) return;
    setPending(true);
    setError(null);
    try {
      const response = await fetch("/v1/auth/login", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (response.ok) {
        setPassword("");
        // A full reload re-runs the session check that guards every other
        // page, so the shell renders from the same source of truth.
        window.location.assign("/");
        return;
      }
      setError(
        response.status === 429
          ? t("localSignIn.errorRateLimited")
          : response.status >= 500
            ? t("localSignIn.errorUnavailable")
            : t("localSignIn.errorGeneric"),
      );
    } catch {
      setError(t("localSignIn.errorNetwork"));
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-5 text-left">
      <div className="space-y-1.5">
        <label
          htmlFor={emailId}
          className="block text-[0.8125rem] font-semibold text-ink"
        >
          {t("localSignIn.email")}
        </label>
        <span className="relative block">
          <Mail
            aria-hidden
            className="pointer-events-none absolute top-1/2 left-3.5 h-4 w-4 -translate-y-1/2 text-ink-muted"
          />
          <input
            id={emailId}
            name="email"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? errorId : undefined}
            placeholder={t("localSignIn.emailPlaceholder")}
            className="h-12 w-full rounded-xl border border-border bg-surface pl-10 text-[0.9375rem] text-ink shadow-sm transition-colors placeholder:text-ink-muted hover:border-border-strong focus-visible:border-brand focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-brand/30 pr-3"
          />
        </span>
      </div>

      <div className="space-y-1.5">
        <label
          htmlFor={passwordId}
          className="block text-[0.8125rem] font-semibold text-ink"
        >
          {t("localSignIn.password")}
        </label>
        <span className="relative block">
          <Lock
            aria-hidden
            className="pointer-events-none absolute top-1/2 left-3.5 h-4 w-4 -translate-y-1/2 text-ink-muted"
          />
          <input
            id={passwordId}
            name="password"
            type={reveal ? "text" : "password"}
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? errorId : undefined}
            className="h-12 w-full rounded-xl border border-border bg-surface pl-10 text-[0.9375rem] text-ink shadow-sm transition-colors placeholder:text-ink-muted hover:border-border-strong focus-visible:border-brand focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-brand/30 pr-11"
          />
          <button
            type="button"
            onClick={() => setReveal(!reveal)}
            aria-label={
              reveal
                ? t("localSignIn.hidePassword")
                : t("localSignIn.showPassword")
            }
            aria-pressed={reveal}
            className="absolute top-1/2 right-2 inline-flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-lg text-ink-muted hover:bg-surface-hover hover:text-ink"
          >
            {reveal ? (
              <EyeOff aria-hidden className="h-4 w-4" />
            ) : (
              <Eye aria-hidden className="h-4 w-4" />
            )}
          </button>
        </span>
      </div>

      {error ? (
        // assertive: a failed sign-in is the only thing the user is waiting
        // on, so it should interrupt rather than queue behind other updates.
        <p
          id={errorId}
          role="alert"
          aria-live="assertive"
          className="rounded-lg bg-critical-soft px-3 py-2 text-[0.8125rem] font-medium text-critical"
        >
          {error}
        </p>
      ) : null}

      <button
        type="submit"
        disabled={pending}
        className="group inline-flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-brand px-5 text-[0.9375rem] font-semibold text-ink-inverse shadow-lg shadow-brand/25 transition-all hover:bg-brand-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand disabled:cursor-not-allowed disabled:opacity-60"
      >
        {pending ? (
          <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
        ) : null}
        {pending ? t("localSignIn.submitting") : t("localSignIn.submit")}
        {pending ? null : (
          <ArrowRight
            aria-hidden
            className="h-4 w-4 transition-transform group-hover:translate-x-0.5"
          />
        )}
      </button>
    </form>
  );
}

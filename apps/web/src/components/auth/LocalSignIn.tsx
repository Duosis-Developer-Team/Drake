"use client";

import { useId, useState } from "react";

/**
 * Email and password sign-in, shown inside the signed-out card when the
 * deployment verifies credentials itself rather than delegating to an
 * identity provider.
 *
 * The server answers every failure identically, and so does this form: one
 * message, whatever went wrong. Saying "no such account" here would undo
 * the care taken on the server not to confirm who has access.
 */

const GENERIC_ERROR = "That email and password did not match. Please try again.";

export function LocalSignIn() {
  const emailId = useId();
  const passwordId = useId();
  const errorId = useId();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

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
          ? "Too many attempts. Please wait a few minutes and try again."
          : response.status >= 500
            ? "Sign-in is unavailable right now. Please try again shortly."
            : GENERIC_ERROR,
      );
    } catch {
      setError("Could not reach Drake. Check your connection and try again.");
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={submit} noValidate className="mt-6 space-y-4 text-left">
      <div className="space-y-1">
        <label htmlFor={emailId} className="block text-sm font-medium text-ink">
          Email
        </label>
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
          className="h-10 w-full rounded-lg border border-border bg-surface px-3 text-sm text-ink focus-visible:outline-2 focus-visible:outline-accent"
        />
      </div>

      <div className="space-y-1">
        <label htmlFor={passwordId} className="block text-sm font-medium text-ink">
          Password
        </label>
        <input
          id={passwordId}
          name="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          className="h-10 w-full rounded-lg border border-border bg-surface px-3 text-sm text-ink focus-visible:outline-2 focus-visible:outline-accent"
        />
      </div>

      {error ? (
        // assertive: a failed sign-in is the only thing the user is waiting
        // on, so it should interrupt rather than queue behind other updates.
        <p id={errorId} role="alert" aria-live="assertive" className="text-sm text-danger">
          {error}
        </p>
      ) : null}

      <button
        type="submit"
        disabled={pending}
        className="inline-flex h-10 w-full items-center justify-center rounded-lg bg-accent px-5 text-sm font-medium text-white transition-opacity hover:opacity-90 focus-visible:outline-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-60"
      >
        {pending ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}

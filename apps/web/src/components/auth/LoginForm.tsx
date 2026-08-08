"use client";

import { useId, useState } from "react";

/**
 * Email and password sign-in.
 *
 * The server answers every failure identically, and so does this form: one
 * message, whatever went wrong. Saying "no such account" here would undo
 * the care taken on the server not to confirm who has access.
 */

const GENERIC_ERROR = "That email and password did not match. Please try again.";

export function LoginForm({ onSignedIn }: { onSignedIn: () => void }) {
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
        // Drop the password from component state before anything re-renders.
        setPassword("");
        onSignedIn();
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
    <main className="flex min-h-screen items-center justify-center bg-slate-950 px-4">
      <form
        onSubmit={submit}
        noValidate
        aria-labelledby="drake-login-heading"
        className="w-full max-w-sm space-y-5 rounded-lg border border-slate-800 bg-slate-900 p-8"
      >
        <div className="space-y-1">
          <h1 id="drake-login-heading" className="text-xl font-semibold text-slate-100">
            Sign in to Drake
          </h1>
          <p className="text-sm text-slate-400">Use your Drake account.</p>
        </div>

        <div className="space-y-1">
          <label htmlFor={emailId} className="block text-sm font-medium text-slate-300">
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
            className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
          />
        </div>

        <div className="space-y-1">
          <label htmlFor={passwordId} className="block text-sm font-medium text-slate-300">
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
            className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
          />
        </div>

        {error ? (
          // assertive: a failed sign-in is the only thing the user is
          // waiting on, so it should interrupt rather than queue.
          <p id={errorId} role="alert" aria-live="assertive" className="text-sm text-rose-400">
            {error}
          </p>
        ) : null}

        <button
          type="submit"
          disabled={pending}
          className="w-full rounded bg-sky-600 px-3 py-2 font-medium text-white hover:bg-sky-500 focus:outline-none focus:ring-2 focus:ring-sky-400 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {pending ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}

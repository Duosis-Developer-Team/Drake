"use client";

import { useCallback, useEffect, useState } from "react";

import { LoginForm } from "./LoginForm";

/**
 * Decides whether to show the application or the sign-in form.
 *
 * The question is answered by asking the API, not by reading a cookie: the
 * session cookie is HttpOnly, so the browser cannot see it, and a client
 * that could would only be guessing. `/v1/me` is the authority — it either
 * returns the caller's identity or 401.
 */

type State = "checking" | "signed-in" | "signed-out";

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<State>("checking");

  const check = useCallback(async () => {
    try {
      const response = await fetch("/v1/me", {
        credentials: "include",
        cache: "no-store",
      });
      setState(response.ok ? "signed-in" : "signed-out");
    } catch {
      // Unreachable API is not the same as signed out, but the only thing
      // the user can usefully do is sign in again.
      setState("signed-out");
    }
  }, []);

  useEffect(() => {
    void check();
  }, [check]);

  if (state === "checking") {
    return (
      <div
        role="status"
        aria-live="polite"
        className="flex min-h-screen items-center justify-center bg-slate-950 text-slate-400"
      >
        Loading…
      </div>
    );
  }

  if (state === "signed-out") {
    // Re-check rather than trust the login response: the same call that
    // guards every reload also confirms this one.
    return <LoginForm onSignedIn={() => void check()} />;
  }

  return <>{children}</>;
}

"use client";

/**
 * Session context. The API is the authorization authority — this context only
 * mirrors /v1/me so the UI can shape navigation; hiding UI is never a
 * security boundary.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { ApiError, apiGet, apiMutate, type Me } from "@/lib/api";

export type SessionState =
  | { status: "loading" }
  | { status: "signed-out" }
  | { status: "expired" }
  | { status: "unavailable" }
  | { status: "authenticated"; me: Me };

interface SessionContextValue {
  state: SessionState;
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
  hasPermission: (permission: string) => boolean;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<SessionState>({ status: "loading" });

  const refresh = useCallback(async () => {
    try {
      const me = await apiGet<Me>("/v1/me");
      setState({ status: "authenticated", me });
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        // Expired vs never-signed-in is indistinguishable server-side by
        // design; if we were authenticated before, surface it as expiry.
        setState((previous) =>
          previous.status === "authenticated"
            ? { status: "expired" }
            : { status: "signed-out" },
        );
      } else {
        setState({ status: "unavailable" });
      }
    }
  }, []);

  const signOut = useCallback(async () => {
    setState((previous) => {
      if (previous.status !== "authenticated") return previous;
      void apiMutate("/v1/auth/logout", { csrfToken: previous.me.csrf_token })
        .catch(() => undefined)
        .then(() => setState({ status: "signed-out" }));
      return { status: "loading" };
    });
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const value = useMemo<SessionContextValue>(
    () => ({
      state,
      refresh,
      signOut,
      hasPermission: (permission: string) =>
        state.status === "authenticated" && state.me.permissions.includes(permission),
    }),
    [state, refresh, signOut],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const context = useContext(SessionContext);
  if (!context) throw new Error("useSession must be used inside SessionProvider");
  return context;
}

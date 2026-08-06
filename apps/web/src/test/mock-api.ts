/** Test helper: route-based fetch mock for session/admin tests. */
import { vi } from "vitest";

import type { Me } from "@/lib/api";

export interface MockRoute {
  status: number;
  body: unknown;
}

export function makeMe(overrides: Partial<Me> = {}): Me {
  return {
    identity: {
      display_name: "Owner One",
      email: "owner@example.test",
      issuer: "http://fake-oidc.test",
    },
    groups_overage: false,
    permissions: [],
    scopes: {},
    csrf_token: "csrf-test-token",
    ...overrides,
  };
}

export interface FetchCall {
  path: string;
  init?: RequestInit;
}

export function installFetchMock(routes: Record<string, MockRoute>): FetchCall[] {
  const calls: FetchCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).split("?")[0];
      calls.push({ path: String(input), init });
      const route = routes[path];
      if (!route) {
        return new Response(
          JSON.stringify({ error: { code: "not_found", message: "not found" } }),
          { status: 404, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(JSON.stringify(route.body), {
        status: route.status,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  return calls;
}

export function errorBody(code: string, message: string): unknown {
  return { error: { code, message, correlation_id: "test" } };
}

import { describe, expect, it, vi } from "vitest";

import { apiGet, apiMutate } from "@/lib/api";

/**
 * The browser talks to the Drake API at same-origin `/v1`.
 *
 * This is not a style preference. A single origin is what makes the
 * session cookie and the CSRF token work without CORS, and routing `/v1`
 * at the ingress (rather than through the Next.js proxy) is what lets a
 * client disconnect actually cancel work server-side — the proxy hop
 * drains the upstream response instead of propagating the abort.
 */
describe("production API routing", () => {
  it("issues same-origin relative requests", async () => {
    const seen: string[] = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
      seen.push(String(input));
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });

    await apiGet("/v1/projects");
    await apiMutate("/v1/roles", { csrfToken: "t", body: { name: "x" } });

    expect(seen).toEqual(["/v1/projects", "/v1/roles"]);
    for (const url of seen) {
      expect(url.startsWith("/v1/")).toBe(true);
      expect(url).not.toMatch(/^https?:\/\//);
    }
    vi.unstubAllGlobals();
  });
});

describe("client bundle contains no API origin", () => {
  it("never references an internal Service name or an absolute API URL", async () => {
    // The source of truth is the shipped source: an absolute API origin
    // anywhere in `src/` would be baked into the bundle and would break
    // same-origin cookies the moment it disagreed with the public host.
    const { readFileSync, readdirSync, statSync } = await import("node:fs");
    const { join } = await import("node:path");

    const offenders: string[] = [];
    const walk = (dir: string) => {
      for (const entry of readdirSync(dir)) {
        const full = join(dir, entry);
        if (statSync(full).isDirectory()) {
          // Test sources are not part of the shipped bundle, and this
          // file necessarily contains the patterns it searches for.
          if (entry !== "test") walk(full);
          continue;
        }
        if (!/\.(ts|tsx)$/.test(entry)) continue;
        const text = readFileSync(full, "utf8");
        if (/DRAKE_API_URL|NEXT_PUBLIC_[A-Z_]*API|drake-api\.[a-z-]+\.svc/.test(text)) {
          offenders.push(full);
        }
      }
    };
    walk(join(process.cwd(), "src"));
    expect(offenders).toEqual([]);
  });
});

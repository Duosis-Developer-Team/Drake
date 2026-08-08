import type { NextConfig } from "next";

/**
 * The web app has exactly one data source: the Drake API, reached at
 * same-origin `/v1`. No absolute API URL is ever baked into the client
 * bundle, so cookies and CSRF work without CORS and there is no second
 * public hostname to keep in sync.
 *
 * The rewrite below exists ONLY for development and E2E, where there is no
 * ingress in front of the two processes. It is deliberately absent in
 * production, for a reason that is not cosmetic:
 *
 * Next's proxy hop does NOT propagate client aborts upstream — it drains
 * the upstream response to reuse the pooled connection (a Route Handler
 * forwarding `request.signal` behaves the same in the node runtime).
 * Server-side query cancellation on client disconnect therefore requires
 * the ingress to route `/v1/*` DIRECTLY to the Drake API, which is what
 * ADR-0021 freezes. Leaving the rewrite enabled in production would
 * silently reintroduce the hop and with it the Sprint 3 cancellation
 * regression.
 */
const isProduction = process.env.DRAKE_DEPLOYMENT_MODE === "production";
const drakeApiBase = process.env.DRAKE_API_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Standalone output only for the container build: it emits a
  // self-contained server with exactly the modules it needs, which is what
  // makes the image independent of the pnpm workspace symlink layout.
  // Dev and E2E keep the normal `next start` path.
  ...(isProduction ? { output: "standalone" as const } : {}),
  async rewrites() {
    if (isProduction) {
      // The ingress owns /v1. Nothing to proxy.
      return [];
    }
    return [{ source: "/v1/:path*", destination: `${drakeApiBase}/v1/:path*` }];
  },
};

export default nextConfig;

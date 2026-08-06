import type { NextConfig } from "next";

/**
 * The web app has exactly one data source: the Drake API.
 * The single rewrite below proxies /v1/* to the Drake API server-side so the
 * browser stays same-origin (cookies + CSRF work, no absolute URLs in client
 * code). No rewrites/proxies to telemetry providers are ever configured here.
 *
 * KNOWN LIMITATION (verified empirically): Next's proxy hop does NOT
 * propagate client aborts upstream — it drains the upstream response to
 * reuse the pooled connection (a Route Handler forwarding request.signal
 * behaves the same in the node runtime). Server-side query cancellation on
 * client disconnect therefore requires the deployment ingress to route
 * /v1/* DIRECTLY to the Drake API (path-based routing), which is the
 * deployment plan; this local rewrite exists only for dev/E2E same-origin
 * convenience.
 */
const drakeApiBase = process.env.DRAKE_API_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/v1/:path*", destination: `${drakeApiBase}/v1/:path*` }];
  },
};

export default nextConfig;

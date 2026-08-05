import type { NextConfig } from "next";

/**
 * The web app has exactly one data source: the Drake API.
 * The single rewrite below proxies /v1/* to the Drake API server-side so the
 * browser stays same-origin (cookies + CSRF work, no absolute URLs in client
 * code). No rewrites/proxies to telemetry providers are ever configured here.
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

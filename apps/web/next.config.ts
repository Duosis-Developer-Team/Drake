import type { NextConfig } from "next";

/**
 * The web app has exactly one data source: the Drake API.
 * No rewrites/proxies to telemetry providers are ever configured here.
 */
const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
};

export default nextConfig;

import type { NextConfig } from "next";

const INTERNAL = process.env.INTERNAL_API_URL ?? "http://127.0.0.1:8130";

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  devIndicators: false,
  images: { unoptimized: true },
  async headers() {
    return [{ source: "/:path*", headers: [
      { key: "X-Robots-Tag", value: "noindex, nofollow" },
      { key: "X-Frame-Options", value: "DENY" },
      { key: "Referrer-Policy", value: "same-origin" },
    ]}];
  },
  async rewrites() {
    // Dev only. In production nginx routes /api and /health straight to the backend, so the
    // Next server never sits in the path of an SSE stream it would have to buffer.
    if (process.env.NODE_ENV === "production" && !process.env.PROXY_API_IN_PROD) return [];
    return [
      { source: "/api/:path*", destination: `${INTERNAL}/api/:path*` },
      { source: "/health", destination: `${INTERNAL}/health` },
    ];
  },
};

export default nextConfig;

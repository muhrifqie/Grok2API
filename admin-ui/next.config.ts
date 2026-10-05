import type { NextConfig } from "next";

const BACKEND = process.env.BACKEND_URL || "http://localhost:8168";

const nextConfig: NextConfig = {
  // pin root to this app so Turbopack doesn't walk the parent (41MB login.har etc.)
  turbopack: { root: __dirname },
  async rewrites() {
    return [
      { source: "/admin/api/:path*", destination: `${BACKEND}/admin/api/:path*` },
      { source: "/v1/:path*", destination: `${BACKEND}/v1/:path*` },
    ];
  },
};

export default nextConfig;

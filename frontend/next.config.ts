import type { NextConfig } from "next";

// Keep the browser API endpoint in frontend/lib/api.ts (or NEXT_PUBLIC_API_URL)
// so Windows does not silently resolve localhost to IPv6 while the backend is
// deliberately bound to the IPv4 loopback address.
const API_TARGET = "http://127.0.0.1:8001";

const nextConfig: NextConfig = {
  allowedDevOrigins: ["127.0.0.1"],
  experimental: {
    // Next 16 的 rewrite 代理默认只缓冲 10MB 请求体，超过会被截断并 ECONNRESET，
    // 导致所有大于 10MB 的视频导入停在 needs_local_file。与后端单视频 1GB 上限对齐。
    proxyClientMaxBodySize: "1gb",
  },
  env: {
    // 默认空 → 相对同源：浏览器不再对 127.0.0.1:8001 发起跨源环回 fetch
    // （本机 TLS 拦截代理会拦截该环回请求导致"后端未响应"），改由下方 rewrite 服务端代理。
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || "",
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_TARGET}/api/:path*` }];
  },
};

export default nextConfig;

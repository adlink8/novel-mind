/** @type {import('next').NextConfig} */
const nextConfig = {
  agentRules: false,
  typescript: {
    ignoreBuildErrors: true,
  },
  // Phase 41 proof (D-41-05): emit a self-contained `.next/standalone` tree that the
  // Electron proof harness can start on a loopback port without `npm install`. The
  // standalone output does NOT copy public/ or .next/static. The package build script
  // prepares both so every production build is directly launchable.
  output: process.env.VERCEL ? undefined : "standalone",
  // Public tunnel hostnames must be allowed or dev blocks / hangs client assets.
  // 生产/隧道域名由环境变量注入（个人域名不写死进仓库）：
  //   NOVELMIND_TUNNEL_HOSTS=https://a.example.com,https://b.example.com
  allowedDevOrigins: [
    "127.0.0.1",
    "localhost",
    ...(process.env.NOVELMIND_TUNNEL_HOSTS
      ? process.env.NOVELMIND_TUNNEL_HOSTS.split(",").map((h) => h.trim()).filter(Boolean)
      : []),
  ],
  // 代理后端 API，避免跨域（默认本机 8010，避免与 rag-api:8000 冲突）
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.BACKEND_URL || "http://127.0.0.1:8010"}/api/:path*`,
      },
      // 25.2-04：SSE 运行端点直达 agent-service（长连接流绕过 FastAPI 跳转；
      // FastAPI 自有 /api/agent/* 仍走上面的后端 rewrite）。
      {
        source: "/agent/:path*",
        destination: `${process.env.AGENT_SERVICE_URL || "http://127.0.0.1:3100"}/agent/:path*`,
      },
    ];
  },
};

export default nextConfig;

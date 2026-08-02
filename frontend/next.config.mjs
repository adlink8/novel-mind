/** @type {import('next').NextConfig} */
const nextConfig = {
  agentRules: false,
  // Public tunnel hostnames must be allowed or dev blocks / hangs client assets.
  allowedDevOrigins: [
    "127.0.0.1",
    "localhost",
    "novelmind.shuoyan.me",
    "novelmind-api.shuoyan.me",
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

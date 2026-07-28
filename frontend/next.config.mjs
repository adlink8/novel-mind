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
    ];
  },
};

export default nextConfig;

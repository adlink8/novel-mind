/** @type {import('next').NextConfig} */
const nextConfig = {
  // 代理后端 API，避免跨域
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.BACKEND_URL || "http://localhost:8000"}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;

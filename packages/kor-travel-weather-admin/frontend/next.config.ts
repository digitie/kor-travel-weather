import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // 컨테이너 CPU quota와 별개로 정적 빌드 worker 수를 제한한다.
  experimental: { cpus: 2 },
};

export default nextConfig;

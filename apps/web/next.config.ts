import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Pin the workspace root. Without it Turbopack walks up and finds an
  // unrelated lockfile in the home directory, and resolves modules from there.
  turbopack: { root: __dirname },
  /* config options here */
};

export default nextConfig;

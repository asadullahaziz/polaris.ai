/** @type {import('next').NextConfig} */
const nextConfig = {
  // P0 uses direct cross-origin calls to the DRF API (:8000) with CORS +
  // credentials (implementation_plan §4.1). A Next `/api` + `/ws` proxy is the
  // stated alternative, but Next rewrites don't forward WebSocket upgrades, so
  // for the WS-centric spike we connect straight to the backend.
  reactStrictMode: true,
};

export default nextConfig;

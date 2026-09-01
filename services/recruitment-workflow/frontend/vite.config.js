import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev-only: proxies API calls to the real RWS server so the frontend can use
// plain relative fetch("/v1/...") paths identically in dev and (later) production,
// with zero CORS configuration needed on either side.
//
// Target matches infra/docker-compose.yml's host port mapping for
// recruitment-workflow (RECRUITMENT_WORKFLOW_PORT, default 8003 per infra/.env).
// Change RWS_DEV_TARGET if RWS is running elsewhere.
const RWS_DEV_TARGET = process.env.RWS_DEV_TARGET || "http://localhost:8003";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/v1": { target: RWS_DEV_TARGET, changeOrigin: true },
      "/health": { target: RWS_DEV_TARGET, changeOrigin: true },
    },
  },
});

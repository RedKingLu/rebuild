import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies /api → community-backend (8001). In production the app
// calls COMMUNITY_BASE_URL (absolute) — see src/api.ts.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 8081,
    proxy: {
      "/api": {
        target: process.env.COMMUNITY_BACKEND_TARGET || "http://localhost:8001",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),  // /api/docs → /docs (backend has no /api prefix)
      },
    },
  },
});

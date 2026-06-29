import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev the dashboard runs on :5173 and proxies the API (and the live
// WebSocket) to the backend. In production the built assets are served by a
// static server that proxies /api to the backend (see deploy/nginx.conf).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8800",
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: { outDir: "dist", sourcemap: false },
});

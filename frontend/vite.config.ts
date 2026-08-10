import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiTarget = process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8801";

export default defineConfig({
  plugins: [react()],
  build: {
    // Route-level lazy loading keeps the entry chunk near 570 kB; this ceiling
    // remains strict enough to catch a regression toward the former 1.5 MB bundle.
    chunkSizeWarningLimit: 600,
  },
  server: {
    port: 5174,
    allowedHosts: ["duckdock.test"],
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
});

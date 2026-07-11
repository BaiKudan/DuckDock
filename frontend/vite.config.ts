import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiTarget = process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8801";

export default defineConfig({
  plugins: [react()],
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

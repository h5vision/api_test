import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [tailwindcss()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/admin-api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/admin-api/, "/v1/admin"),
        headers: { "X-Vision-Admin-Proxy": "dashboard-internal" },
      },
    },
  },
  preview: { host: "0.0.0.0", port: 4173 },
});

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // 监听 0.0.0.0：局域网内其它设备可通过本机 IP:5173 访问；本机仍可用 localhost
    host: true,
    strictPort: false,
    proxy: {
      "/api": "http://localhost:8765",
      "/storage": "http://localhost:8765",
    },
  },
  preview: {
    host: true,
    port: 4173,
  },
});

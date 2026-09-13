import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5180,
    proxy: {
      "/api": {
        // Overridable so a second dev instance (another PORT for the api,
        // --port for vite) can run beside one that is already up.
        target: process.env.LLR_API_URL ?? "http://localhost:8790",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  build: {
    target: "esnext",
  },
});

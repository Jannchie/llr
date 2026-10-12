import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

// Overridable so a second dev instance (another PORT for the api, --port for
// vite) can run beside one that is already up.
const apiTarget = process.env.LLR_API_URL ?? "http://localhost:8790";

export default defineConfig(({ command }) => ({
  plugins: [vue()],
  // Where a page opened on this machine can reach the API without the proxy
  // (api.ts bulkBase): a 33 MP decode is ~200 MB, and the proxy's re-chunking
  // costs half a second of it. 127.0.0.1 because that is what the API binds,
  // and "localhost" may resolve to ::1 first.
  define: command === "serve"
    ? { "import.meta.env.VITE_API_DIRECT": JSON.stringify(apiTarget.replace("//localhost", "//127.0.0.1")) }
    : {},
  server: {
    port: 5180,
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  build: {
    target: "esnext",
  },
}));

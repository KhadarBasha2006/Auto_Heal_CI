import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// During local development the dashboard talks to the FastAPI backend on
// :8000. In production the same paths are served by Vercel rewrites (see
// vercel.json), so the frontend always calls same-origin /api/* and never
// needs the backend URL baked into the bundle.
const apiProxy = {
  "/api": {
    target: process.env.VITE_DEV_API_TARGET || "http://127.0.0.1:8000",
    changeOrigin: true,
    // Server-Sent Events must not be buffered by the proxy, or the live
    // dashboard would only update once the run had already finished.
    configure: (proxy) => {
      proxy.on("proxyRes", (proxyRes) => {
        const type = proxyRes.headers["content-type"] || "";
        if (type.includes("text/event-stream")) {
          proxyRes.headers["cache-control"] = "no-cache, no-transform";
        }
      });
    },
  },
};

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: apiProxy },
  preview: { port: 4173, proxy: apiProxy },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});

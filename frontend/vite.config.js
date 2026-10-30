import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The frontend calls /api/... on its OWN origin and Vite forwards those requests
// to the FastAPI backend. Same-origin requests need no CORS and are never blocked
// by browser privacy features or extensions that block cross-port localhost calls.
const api = {
  "/api": {
    target: "http://127.0.0.1:8000",
    changeOrigin: true,
    rewrite: (path) => path.replace(/^\/api/, ""),
  },
};

export default defineConfig({
  plugins: [react()],
  server: {
    host: "localhost",
    port: 5173,
    strictPort: true, // fail instead of silently moving to 5174
    proxy: api,
  },
  preview: { port: 4173, proxy: api },
});

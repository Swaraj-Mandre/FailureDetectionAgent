import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the page runs on :5173 and passes /api calls to the Python server on :8000.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8000" } },
});

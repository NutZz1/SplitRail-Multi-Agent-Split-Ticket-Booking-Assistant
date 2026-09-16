import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: `npm run dev` on :5173, calling the FastAPI dev server directly (VITE_API_BASE).
// Prod: `npm run build` -> client/dist, served by FastAPI on the same origin, so
// fetch() uses relative /api/... paths and no CORS is involved.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
  build: { outDir: "dist", emptyOutDir: true },
});

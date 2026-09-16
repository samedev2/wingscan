import { defineConfig } from "vite";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const scRoot = path.resolve(__dirname, "..", "spatial-controls");

// spatial-controls v6 está em src/ (ESM com imports .js). Apontamos o alias
// direto pro source para evitar ter que buildar a lib com TypeScript 6.x
// (que ainda não existe no npm). O Vite/esbuild resolve os imports .js
// transparentemente via `moduleResolution: "Bundler"`.
export default defineConfig({
  resolve: {
    alias: [
      // spatial-controls v6 está em src/ (ESM com imports .js). Apontamos o alias
      // direto pro source para evitar ter que buildar a lib com TypeScript 6.x
      // (que ainda não existe no npm). O Vite/esbuild resolve os imports .js
      // transparentemente via `moduleResolution: "Bundler"`.
      { find: "spatial-controls", replacement: path.resolve(scRoot, "src", "index.ts") },
      // spatial-controls importa `synthetic-event` mas seu node_modules não tem —
      // o pacote vive em web/node_modules. Forçamos o caminho absoluto.
      { find: /^synthetic-event$/, replacement: path.resolve(__dirname, "node_modules", "synthetic-event") },
    ],
  },
  optimizeDeps: {
    exclude: ["spatial-controls"],
    include: ["synthetic-event", "three"],
  },
  server: {
    host: "127.0.0.1",
    port: 5175,
    strictPort: true,
    proxy: {
      // Proxy para o cv-service em :8000 — evita CORS no front.
      "/cv": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/cv/, ""),
        ws: true,
      },
    },
  },
});

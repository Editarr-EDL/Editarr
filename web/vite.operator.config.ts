import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  root: fileURLToPath(new URL("./operator", import.meta.url)),
  base: "/admin/",
  publicDir: false,
  plugins: [react()],
  build: { outDir: "../dist-operator", emptyOutDir: true, assetsInlineLimit: 0 },
  server: { host: "127.0.0.1", strictPort: true, fs: { allow: [fileURLToPath(new URL(".", import.meta.url))] } },
});
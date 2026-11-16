import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { developmentLoopbackRedirect } from "./src/shared/development-origin";

const canonicalPath = /^\/(?:[A-Za-z0-9_~.-]+(?:\/[A-Za-z0-9_~.-]+)*)?$/;

function developmentBasePath(): string {
  const value = process.env.EDITARR_VITE_BASE_PATH ?? "/";
  const segments = value.split("/").slice(1);
  if (!canonicalPath.test(value) || segments.includes(".") || segments.includes("..")) {
    throw new Error(`EDITARR_VITE_BASE_PATH is not canonical: ${value}`);
  }
  return value;
}

export default defineConfig(({ command }) => {
  const basePath = developmentBasePath();
  const apiBasePath = basePath === "/" ? "/api" : `${basePath}/api`;
  const apiTarget = process.env.EDITARR_VITE_API_TARGET ?? "http://127.0.0.1:3348";
  const webPort = Number(process.env.EDITARR_VITE_WEB_PORT ?? "5173");
  const webHost = process.env.EDITARR_DEVELOPMENT_WEB_HOST ?? "localhost";
  return {
    base: "./",
    plugins: [
      react(),
      {
        name: "editarr-development-bootstrap",
        configureServer(server) {
          server.middlewares.use((request, response, next) => {
            const location = developmentLoopbackRedirect(
              request.headers.host,
              request.url,
              webHost,
              webPort,
            );
            if (location === null) {
              next();
              return;
            }
            response.statusCode = 307;
            response.setHeader("Location", location);
            response.end();
          });
        },
        transformIndexHtml(html) {
          if (command !== "serve") return html;
          const bootstrap = JSON.stringify({ version: 1, basePath, apiBasePath });
          return html.replace(
            /<script\b(?=[^>]*\bid=["']editarr-bootstrap["'])[^>]*>.*?<\/script>/s,
            `<script id="editarr-bootstrap" type="application/json" data-editarr-bootstrap="development">${bootstrap}</script>`,
          );
        },
      },
    ],
    server: {
      port: webPort,
      strictPort: true,
      allowedHosts: true,
      proxy: {
        [apiBasePath]: {
          target: apiTarget,
          changeOrigin: false,
          xfwd: false,
        },
      },
    },
    test: {
      environment: "jsdom",
      setupFiles: "./src/test/setup.ts",
      include: ["src/**/*.test.{ts,tsx}"],
      coverage: {
        provider: "v8",
        reporter: ["text", "json-summary"],
        thresholds: {
          statements: 65,
          branches: 60,
          functions: 60,
          lines: 70,
        },
      },
    },
  };
});

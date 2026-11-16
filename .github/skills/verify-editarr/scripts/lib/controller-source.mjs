import { spawn } from "node:child_process";
import path from "node:path";

const [python, apiPort, webPort, apiScript] = process.argv.slice(2);
const scratch = path.dirname(process.env.EDITARR_DATA_DIR);
const environment = {
  ...process.env,
  HOME: path.join(scratch, "home"),
  XDG_CACHE_HOME: path.join(scratch, "cache"),
  EDITARR_BASE_URL: `http://127.0.0.1:${webPort}`,
  EDITARR_AUTH_COOKIE_SECURE: "false",
  EDITARR_DEVELOPMENT_WEB_HOST: "127.0.0.1",
  EDITARR_VITE_API_TARGET: `http://127.0.0.1:${apiPort}`,
  EDITARR_VITE_WEB_PORT: webPort,
};
for (const name of ["HF_HOME", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE", "TORCH_HOME", "CT2_CACHE_DIR"]) delete environment[name];
const children = [
  spawn(python, apiScript ? [apiScript, apiPort] : ["-m", "scanner.cli", "--api-only", "--foreground", "--no-browser", "--host", "127.0.0.1", "--port", apiPort], { env: environment, stdio: "inherit" }),
  spawn("pnpm", ["dev", "--host", "127.0.0.1", "--port", webPort, "--strictPort"], { cwd: "web", env: environment, stdio: "inherit" }),
];
let stopping = false;
function stop() {
  if (stopping) return;
  stopping = true;
  for (const child of children) if (child.exitCode === null) child.kill("SIGTERM");
}
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, stop);
for (const child of children) {
  child.on("error", (error) => { console.error(error.message); process.exitCode = 1; stop(); });
  child.on("exit", (code) => { if (!stopping) process.exitCode = code || 1; stop(); });
}
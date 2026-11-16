import { execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";

export const pythonPath = root => path.join(root, ".venv/bin/python");
/** Where every journey keeps its evidence: `VERIFY_EVIDENCE_ROOT` (relative to the checkout) or the default. */
export const evidenceRoot = root => path.resolve(root, process.env.VERIFY_EVIDENCE_ROOT || "web/test-results/verify-editarr");
const HOST_CACHE_VARIABLES = ["HF_HOME", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE", "TORCH_HOME", "CT2_CACHE_DIR", "XDG_CACHE_HOME"];

/**
 * Build the environment for every Python and pnpm child of a run.
 * PYTHONPATH pins imports to this checkout even when the shared virtual
 * environment holds an editable install of another checkout. Logs go to a
 * `logs` directory beside `data`, never the host's default log directory.
 * HOME and cache roots also move beside `data`, because the native model and
 * video caches derive from HOME rather than EDITARR_DATA_DIR.
 * pnpm must not install dependencies before running Vite.
 */
export function childEnvironment(root, data) {
  const environment = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.toUpperCase().startsWith("EDITARR_") && !HOST_CACHE_VARIABLES.includes(key)));
  Object.assign(environment, { PYTHONPATH: root, pnpm_config_verify_deps_before_run: "false", BROWSER: "false", PYTHONUNBUFFERED: "1" });
  if (data) {
    const scratch = path.dirname(data);
    Object.assign(environment, { EDITARR_DATA_DIR: data, EDITARR_LOG_DIR: runLogs(data), HOME: path.join(scratch, "home"), XDG_CACHE_HOME: path.join(scratch, "cache") });
  }
  return environment;
}

export const runLogs = data => path.join(path.dirname(data), "logs");

function onPath(name) {
  for (const directory of (process.env.PATH ?? "").split(path.delimiter).filter(Boolean)) {
    try { fs.accessSync(path.join(directory, name), fs.constants.X_OK); return true; }
    catch { continue; }
  }
  return false;
}

function python(root, code) {
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "editarr-verify-check-"));
  try {
    return execFileSync(pythonPath(root), ["-P", "-c", code], {
      cwd: root, env: childEnvironment(root, path.join(scratch, "data")), encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], timeout: 30000,
    }).trim();
  } finally { fs.rmSync(scratch, { recursive: true }); }
}

function firstLine(error) {
  return String(error.stderr || error.message).trim().split("\n").at(-1);
}

/** Each check returns null when satisfied or the exact observed gap. `needs` skips checks that cannot run. */
const REQUIREMENTS = {
  posix: {
    check: () => process.platform === "win32" ? "Windows lacks the POSIX process groups, ps, and lsof this helper uses" : null,
    remedy: "Run on macOS or Linux.",
  },
  python: {
    check: root => { try { fs.accessSync(pythonPath(root), fs.constants.X_OK); return null; } catch { return `${pythonPath(root)} is not executable`; } },
    remedy: "Provision this checkout's .venv with `uv sync --extra dev`.",
  },
  checkoutImports: {
    needs: ["python"],
    check: root => {
      try {
        const origin = python(root, "import scanner, uvicorn; print(scanner.__file__)");
        return origin.startsWith(`${root}${path.sep}`) ? null : `scanner imports from ${origin}, outside ${root}`;
      } catch (error) { return `Python cannot import scanner and uvicorn: ${firstLine(error)}`; }
    },
    remedy: "Provision this checkout's .venv with `uv sync --extra dev`.",
  },
  pnpm: { check: () => onPath("pnpm") ? null : "pnpm is not on PATH", remedy: "Install the pnpm version pinned in web/package.json." },
  lsof: { check: () => onPath("lsof") ? null : "lsof is not on PATH", remedy: "Install lsof." },
  ps: { check: () => onPath("ps") ? null : "ps is not on PATH", remedy: "Install procps." },
  webDependencies: {
    check: root => fs.existsSync(path.join(root, "web/node_modules/vite/package.json")) ? null : "web dependencies are not installed in this checkout",
    remedy: "`pnpm --dir web install`",
  },
  chromium: {
    check: root => {
      try {
        const { chromium } = createRequire(path.join(root, "web/package.json"))("@playwright/test");
        return fs.existsSync(chromium.executablePath()) ? null : `Playwright Chromium is missing at ${chromium.executablePath()}`;
      } catch (error) { return `@playwright/test cannot load: ${firstLine(error)}`; }
    },
    remedy: "`pnpm --dir web exec playwright install chromium`",
  },
  ffmpeg: {
    needs: ["python", "checkoutImports"],
    check: root => {
      try {
        const ffmpeg = python(root, "from scanner.runtime.runtime_support import resolve_ffmpeg; print(resolve_ffmpeg())");
        return fs.existsSync(ffmpeg) ? null : `resolved FFmpeg ${ffmpeg} does not exist`;
      } catch (error) { return `Editarr cannot resolve FFmpeg: ${firstLine(error)}`; }
    },
    remedy: "Provision FFmpeg for the source install (imageio-ffmpeg or IMAGEIO_FFMPEG_EXE).",
  },
  jellyfinImage: {
    needs: ["python", "checkoutImports"],
    check: root => {
      const scripts = path.join(root, ".github/skills/verify-editarr/scripts");
      let image;
      try { image = execFileSync(pythonPath(root), ["-B", "-c", "from media_access_live_docker import IMAGE; print(IMAGE)"], { cwd: scripts, env: { ...childEnvironment(root), PYTHONPATH: `${root}${path.delimiter}${scripts}` }, encoding: "utf8" }).trim(); }
      catch (error) { return `cannot read the pinned image: ${firstLine(error)}`; }
      try { execFileSync("docker", ["image", "inspect", image], { stdio: "ignore", timeout: 30000 }); return null; }
      catch { return `docker cannot inspect ${image}`; }
    },
    remedy: "Start Docker and provide the digest-pinned Jellyfin 12.0.0 image; setup signs in a Jellyfin administrator.",
  },
};

const launch = ["posix", "python", "checkoutImports", "pnpm", "lsof", "ps", "webDependencies"];
export const MODE_REQUIREMENTS = {
  probe: launch,
  serve: launch,
  auth: [...launch, "chromium", "jellyfinImage"],
  upgrade: [...launch, "chromium"],
  repairs: [...launch, "chromium"],
  restore: [...launch, "chromium"],
  pair: [...launch, "chromium"],
  doctor: ["posix", "lsof", "ps"],
  media: ["posix", "lsof", "ps", "python", "checkoutImports", "ffmpeg"],
};

/** List unmet prerequisites for a mode without launching or contacting any instance. */
export function missingPrerequisites(mode, root) {
  const missing = [];
  for (const name of MODE_REQUIREMENTS[mode]) {
    if (REQUIREMENTS[name].needs?.some(dependency => missing.some(gap => gap.requirement === dependency))) continue;
    const detail = REQUIREMENTS[name].check(root);
    if (detail) missing.push({ requirement: name, detail, remedy: REQUIREMENTS[name].remedy });
  }
  return missing;
}

/**
 * The `--community` double judges requests with the community service's own
 * validators, which `community-contract.mjs` bundles with esbuild and zod from
 * `community-service/node_modules`. Building that bundle is the check.
 */
export function communityPrerequisites(root) {
  const contract = path.join(root, ".github/skills/verify-editarr/scripts/lib/community-contract.mjs");
  const result = spawnSync(process.execPath, [contract, "--check"], { cwd: root, encoding: "utf8", stdio: ["ignore", "ignore", "pipe"], timeout: 60000 });
  if (result.status === 0) return [];
  const detail = String(result.stderr || result.error?.message || `exit ${result.status ?? result.signal}`).trim().split("\n")[0];
  return [{ requirement: "communityContract", detail, remedy: "`pnpm --dir community-service install --frozen-lockfile`, which provides esbuild and zod." }];
}

/** Record a BLOCKED outcome in new evidence and exit 2. Nothing was launched or attached. */
export function exitBlocked(root, mode, missing) {
  fs.mkdirSync(evidenceRoot(root), { recursive: true });
  const evidence = fs.mkdtempSync(path.join(evidenceRoot(root), "blocked-"));
  const record = { status: "BLOCKED", mode, missing, launched: false, attachedToExistingInstance: false, remediesRun: false, checkedAt: new Date().toISOString() };
  fs.writeFileSync(path.join(evidence, "blocked.json"), `${JSON.stringify(record, null, 2)}\n`, { mode: 0o600 });
  console.error(`Evidence: ${evidence}`);
  for (const { requirement, detail, remedy } of missing) console.error(`BLOCKED ${requirement}: ${detail}. Remedy (not run): ${remedy}`);
  process.exit(2);
}

import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { childEnvironment, pythonPath } from "./prerequisites.mjs";

// Same instant as _NOW in tests/test_auth_services.py, so browser and service proofs agree.
export const FIXED_TIME = "2026-01-01T12:00:00.000Z";

/** Pin browser Date for every page in one context. Timers still run; server time is unaffected. */
export async function installFixedClock(context) {
  await context.clock.setFixedTime(new Date(FIXED_TIME));
}

const generator = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../synthetic_media.py");

/**
 * Write the synthetic media fixture under `scratch/media` with the app's own
 * FFmpeg resolution and audio probe. `data` keeps runtime paths inside scratch.
 */
export function synthesizeMedia(root, scratch, data) {
  const directory = path.join(scratch, "media");
  fs.mkdirSync(directory, { recursive: true });
  const output = execFileSync(pythonPath(root), ["-P", generator, directory], {
    cwd: root, env: childEnvironment(root, data), encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], timeout: 120000,
  });
  return JSON.parse(output);
}

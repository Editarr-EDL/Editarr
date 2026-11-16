import path from "node:path";
import { fileURLToPath } from "node:url";
import { parsePortRange } from "./ports.mjs";

const scripts = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

/** Test files that launch servers. `node --test` runs files concurrently, so each owns a slice. */
export const LAUNCHING_TEST_FILES = ["verify.test.mjs", "lib/instance-signals.test.mjs"];

/** The slice of `env.VERIFY_PORTS` owned by the test file at `fileUrl`; unset means ephemeral ports. */
export function testPortRange(fileUrl, env = process.env) {
  const configured = env.VERIFY_PORTS;
  const range = parsePortRange(configured);
  if (!range) return undefined;
  const file = path.relative(scripts, fileURLToPath(fileUrl));
  const index = LAUNCHING_TEST_FILES.indexOf(file);
  if (index < 0) throw new Error(`${file} is not a registered server-launching test file`);
  const size = Math.floor((range[1] - range[0] + 1) / LAUNCHING_TEST_FILES.length);
  if (size < 3) throw new Error(`VERIFY_PORTS=${configured} is too small for ${LAUNCHING_TEST_FILES.length} files of three ports`);
  const first = range[0] + index * size;
  return `${first}-${first + size - 1}`;
}

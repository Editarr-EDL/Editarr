import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import { parsePortRange } from "./ports.mjs";
import { LAUNCHING_TEST_FILES, testPortRange } from "./test-ports.mjs";

const scripts = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const fileUrl = file => pathToFileURL(path.join(scripts, file)).href;

test("server-launching test files get disjoint subranges of VERIFY_PORTS", () => {
  for (const configured of ["9250-9259", "9240-9279"]) {
    const [first, last] = parsePortRange(configured);
    const taken = new Set();
    for (const file of LAUNCHING_TEST_FILES) {
      const [low, high] = parsePortRange(testPortRange(fileUrl(file), { VERIFY_PORTS: configured }));
      assert.ok(low >= first && high <= last, `${file} stays inside ${configured}`);
      for (let port = low; port <= high; port++) {
        assert.ok(!taken.has(port), `${port} is issued to two files`);
        taken.add(port);
      }
    }
  }
  assert.equal(testPortRange(fileUrl(LAUNCHING_TEST_FILES[0]), {}), undefined, "no range means ephemeral ports");
  assert.throws(() => testPortRange(fileUrl("lib/database.test.mjs"), { VERIFY_PORTS: "9250-9259" }), /not a registered server-launching test file/);
  assert.throws(() => testPortRange(fileUrl(LAUNCHING_TEST_FILES[0]), { VERIFY_PORTS: "9250-9254" }), /too small/);
});

test("no test file takes ports from VERIFY_PORTS except through its own subrange", () => {
  const direct = fs.readdirSync(scripts, { recursive: true })
    .filter(file => file.endsWith(".test.mjs"))
    .filter(file => /process\.env\.VERIFY_PORTS/.test(fs.readFileSync(path.join(scripts, file), "utf8")));
  assert.deepEqual(direct, []);
});

import fs from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import readline from "node:readline";
import { fileURLToPath, pathToFileURL } from "node:url";

const service = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../../../community-service");

/**
 * Bundle the community service's own request validators so the labelled double
 * accepts only bodies the real service would parse. The enrollment payload schema
 * is module-private, so the bundle re-exports it without changing its definition.
 */
async function serviceValidators() {
  const esbuild = createRequire(path.join(service, "package.json"))("esbuild");
  const exposeEnrollment = { name: "expose-enrollment-schema", setup(build) {
    build.onLoad({ filter: /identity[\\/]enrollment-proof\.ts$/ }, args => ({
      contents: `${fs.readFileSync(args.path, "utf8")}\nexport { payloadSchema };\n`, loader: "ts" }));
  } };
  const entry = `
    export { parseMatchRequest } from "./src/matching/input.ts";
    export { parseSubmissionRequest, DEFAULT_SUBMISSION_LIMITS } from "./src/submissions/input.ts";
    export { validatedName, nameKey } from "./src/identity/profile.ts";
    export { rotationIntent } from "./src/identity/rotation-proof.ts";
    export { payloadSchema as enrollmentSchema } from "./src/identity/enrollment-proof.ts";`;
  const built = await esbuild.build({ stdin: { contents: entry, resolveDir: service, loader: "ts" }, bundle: true,
    write: false, format: "esm", platform: "node", logLevel: "silent", plugins: [exposeEnrollment],
    // Prefer ES module builds: jsonc-parser's UMD main cannot be bundled as ESM.
    mainFields: ["module", "main"] });
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "editarr-community-contract-"));
  try {
    fs.writeFileSync(path.join(scratch, "validators.mjs"), built.outputFiles[0].text);
    return await import(pathToFileURL(path.join(scratch, "validators.mjs")).href);
  } finally {
    fs.rmSync(scratch, { recursive: true, force: true });
  }
}

/**
 * Methods and body parsers per path, taken from the real routers in
 * `community-service/src/http`. A parser throws for a body the service rejects
 * and may return facts the double needs, such as a rename's case-folded key.
 */
function routes(v) {
  const unread = () => {};
  const json = body => JSON.parse(body);
  const rename = body => {
    const value = json(body);
    if (!value || typeof value !== "object" || Object.keys(value).length !== 1 || !("displayName" in value)) throw new Error("keys");
    const name = v.validatedName(value.displayName);
    return { displayName: name, nameKey: v.nameKey(name) };
  };
  return [
    { path: /^\/v1\/identity$/, json: { PATCH: true }, methods: { GET: unread, PATCH: rename }, invalid: "invalid_display_name", wrongType: "invalid_rename" },
    { path: /^\/v1\/aliases$/, methods: { GET: unread }, invalid: "invalid_request", bodyless: [400, "invalid_request"] },
    { path: /^\/v1\/match$/, json: { POST: true }, methods: { POST: body => { v.parseMatchRequest(new TextEncoder().encode(body)); } }, invalid: "invalid_match" },
    { path: /^\/v1\/submissions$/, json: { POST: true }, methods: { POST: body => { v.parseSubmissionRequest(json(body), v.DEFAULT_SUBMISSION_LIMITS); } }, invalid: "invalid_submission" },
    { path: /^\/v1\/keys\/(rotate|rotation-status)$/, json: { POST: true }, methods: { POST: body => { v.rotationIntent(json(body)); } }, invalid: "invalid_key_operation" },
    { path: /^\/v1\/enrollments\/complete$/, methods: { POST: body => { v.enrollmentSchema.parse(json(body)); } }, invalid: "invalid_enrollment_proof", wrongMethod: [400, "invalid_enrollment_proof"] },
  ];
}

/** Judge one request: `{ status: 200, facts }` when the real service would parse it. */
export function judge(table, { method, path: requestPath, contentType, body }) {
  const route = table.find(entry => entry.path.test(requestPath));
  if (!route) return { status: 404, code: "not_found" };
  const parse = route.methods[method];
  if (!parse) return { status: route.wrongMethod?.[0] ?? 405, code: route.wrongMethod?.[1] ?? "method_not_allowed" };
  const invalid = route.invalid;
  if (route.bodyless && body.length) return { status: route.bodyless[0], code: route.bodyless[1] };
  if (route.json?.[method] && contentType !== "application/json") return { status: 400, code: route.wrongType ?? invalid };
  try {
    return { status: 200, facts: parse(body) ?? null };
  } catch {
    return { status: 400, code: invalid };
  }
}

// `--check` only builds the validators, so callers can report a missing esbuild or zod before launching anything.
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  let table;
  try { table = routes(await serviceValidators()); }
  catch (error) {
    console.error(`cannot bundle the community service validators: ${String(error.message).split("\n")[0]}`);
    process.exit(1);
  }
  if (process.argv[2] === "--check") process.exit(0);
  process.stdout.write(JSON.stringify({ ready: true }) + "\n");
  for await (const line of readline.createInterface({ input: process.stdin })) {
    process.stdout.write(JSON.stringify(judge(table, JSON.parse(line))) + "\n");
  }
}

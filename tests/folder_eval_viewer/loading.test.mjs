// Result loading: failed or interrupted reads are never committed, and the next load retries.
import assert from "node:assert/strict";
import { test } from "node:test";

const element = () => ({ textContent: "", hidden: true, open: false, append() {} });
const elements = {};
globalThis.document = { querySelector: (sel) => (elements[sel] ||= element()), body: element() };

const VIEWER = new URL("../../scanner/evaluation/folder_eval/viewer/", import.meta.url);
const { S, allLoaded, loadModel } = await import(new URL("data.js", VIEWER));

const encoder = new TextEncoder();
function response(chunks, { failAfter = false, status = 200 } = {}) {
  let index = 0;
  const body = { getReader: () => ({ read: async () => {
    if (index < chunks.length) return { done: false, value: encoder.encode(chunks[index++]) };
    if (failAfter) throw new Error("network reset");
    return { done: true };
  } }) };
  return { ok: status < 400, status, headers: { get: () => null }, body };
}

test("an interrupted read is not committed and the next load retries", async () => {
  Object.assign(S, { modelIds: ["m"], metas: { m: {} }, results: {}, pending: {}, loadErrors: {}, families: {} });
  const line = JSON.stringify({ id: "a", status: "ok", flagged: ["nudity"], frames: [] }) + "\n";
  globalThis.fetch = async () => response([line], { failAfter: true });
  await assert.rejects(loadModel("m"), /network reset/);
  assert.equal(S.results.m, undefined);
  assert.equal(allLoaded(), false);
  assert.deepEqual(S.pending, {});
  assert.match(S.loadErrors.m, /network reset/);
  assert.match(elements["#toast"].textContent, /Could not load results for m/);

  globalThis.fetch = async () => response([line]);
  const map = await loadModel("m");
  assert.deepEqual(map.get("a").flagged, ["nudity"]);
  assert.equal(allLoaded(), true);
  assert.deepEqual(S.loadErrors, {});
});

test("HTTP errors fail the load but a missing results file reads as empty", async () => {
  Object.assign(S, { modelIds: ["m"], metas: { m: {} }, results: {}, pending: {}, loadErrors: {}, families: {} });
  globalThis.fetch = async () => response([], { status: 500 });
  await assert.rejects(loadModel("m"), /HTTP 500/);
  assert.equal(allLoaded(), false);
  globalThis.fetch = async () => response([], { status: 404 });
  assert.equal((await loadModel("m")).size, 0);
  assert.equal(allLoaded(), true);
});

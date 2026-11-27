// Pure viewer rules (no DOM). Run with `node --test tests/folder_eval_viewer/*.test.mjs`; pytest runs it through
// tests/test_folder_eval_viewer.py.
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

const VIEWER = new URL("../../scanner/evaluation/folder_eval/viewer/", import.meta.url);
const { S } = await import(new URL("data.js", VIEWER));
const { itemCorrect, outcomeFor, supportsKey } = await import(new URL("scoring.js", VIEWER));
const { liveBlock } = await import(new URL("live-metrics.js", VIEWER));
const { isSensitive } = await import(new URL("sensitivity.js", VIEWER));
const { applyCorrections } = await import(new URL("corrections.js", VIEWER));

const GORE = { supports: ["fear/hemophobia"] };
const item = (id, expected, extra = {}) => ({ id, path: `video/x/${id}.mp4`, folder: "video/x", expected, scored: true, ...extra });
const ok = (flagged) => ({ status: "ok", flagged, scores: {} });

beforeEach(() => {
  Object.assign(S, { modelIds: [], metas: {}, results: {}, items: [], corrections: {}, byId: new Map() });
  S.familyOrder = ["violence", "fear", "nudity", "sex"];
});

test("a token-only model does not score the family", () => {
  assert.equal(supportsKey(GORE, "fear"), false);
  assert.equal(supportsKey(GORE, "fear/hemophobia"), true);
  assert.equal(supportsKey({}, "fear"), true);
  const fire = item("fire", ["fear", "fear/fire"]);
  assert.equal(outcomeFor(fire, ok([]), "fear", GORE), "unscored");
  assert.equal(itemCorrect(fire, ok([]), GORE), true);
  const blood = item("blood", ["fear", "fear/hemophobia"]);
  assert.equal(outcomeFor(blood, ok([]), "fear/hemophobia", GORE), "FN");
  assert.equal(itemCorrect(blood, ok(["fear", "fear/hemophobia"]), GORE), true);
  assert.equal(itemCorrect(blood, ok([]), GORE), false);
});

test("live metrics give a token-only model token rows but no family rows", () => {
  S.modelIds = ["gore"];
  S.metas.gore = GORE;
  S.items = [item("fire", ["fear", "fear/fire"]), item("blood", ["fear", "fear/hemophobia"])];
  S.results.gore = new Map([["fire", ok([])], ["blood", ok(["fear", "fear/hemophobia"])]]);
  const block = liveBlock("gore", "all");
  assert.deepEqual(Object.keys(block.families), []);
  assert.deepEqual(Object.keys(block.tokens), ["fear/hemophobia"]);
  assert.equal(block.tokens["fear/hemophobia"].tp, 1);
});

test("sensitivity covers every model's flags and corrections", () => {
  const clip = item("c", ["violence"]);
  S.items = [clip];
  S.byId.set("c", clip);
  S.modelIds = ["siglip2", "nudenet"];
  S.results.siglip2 = new Map([["c", ok(["violence"])]]);
  assert.equal(isSensitive(clip), false);
  S.results.nudenet = new Map([["c", ok(["nudity"])]]);
  assert.equal(isSensitive(clip), true);
  delete S.results.nudenet;
  S.corrections.c = { "sex/sex": true };
  applyCorrections(clip);
  assert.equal(isSensitive(clip), true);
});

test("merged rows recompute scores and flags from the final frames", async () => {
  const { compactResult, mergeResult } = await import(new URL("results.js", VIEWER));
  const meta = { thresholds: { violence: 0.25 } };
  const first = compactResult({ id: "a", status: "ok", flagged: ["violence"], frames: [
    { n: 0, scores: { violence: 0.9 } }, { n: 1, scores: { violence: 0.1 } }] }, meta);
  const second = compactResult({ id: "a", status: "ok", flagged: [], frames: [
    { n: 0, scores: { violence: 0.2 } }, { n: 2, scores: { violence: 0.24 } }] }, meta);
  const merged = mergeResult(first, second, meta, new Set([0, 1]));
  assert.deepEqual(merged.frames.map((f) => f.n), [0, 1, 2]);
  assert.deepEqual(merged.scores, { violence: 0.24 });
  assert.deepEqual(merged.flagged, []);
  assert.equal(merged.best.violence, 2);
  assert.deepEqual(merged.sample.flagged, []);
});

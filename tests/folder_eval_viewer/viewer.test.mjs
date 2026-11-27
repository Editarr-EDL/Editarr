// Pure viewer rules (no DOM). Run with `node --test tests/folder_eval_viewer/`; pytest runs it through
// tests/test_folder_eval_viewer.py.
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

const VIEWER = new URL("../../scanner/evaluation/folder_eval/viewer/", import.meta.url);
const { S } = await import(new URL("data.js", VIEWER));
const { itemCorrect, outcomeFor, supportsKey } = await import(new URL("scoring.js", VIEWER));
const { liveBlock } = await import(new URL("live-metrics.js", VIEWER));

const GORE = { supports: ["fear/hemophobia"] };
const item = (id, expected, extra = {}) => ({ id, path: `video/x/${id}.mp4`, folder: "video/x", expected, scored: true, ...extra });
const ok = (flagged) => ({ status: "ok", flagged, scores: {} });

beforeEach(() => {
  Object.assign(S, { modelIds: [], metas: {}, results: {}, items: [] });
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

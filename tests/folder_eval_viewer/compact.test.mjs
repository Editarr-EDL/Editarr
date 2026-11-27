// Compact per-frame scores: merge/sample parity with the harness and a loose retained-size bound.
import assert from "node:assert/strict";
import { test } from "node:test";
import v8 from "node:v8";
import vm from "node:vm";

const VIEWER = new URL("../../scanner/evaluation/folder_eval/viewer/", import.meta.url);
const { compactResult, frameScores, frameTop, mergeResult } = await import(new URL("results.js", VIEWER));

test("merges and samples match the harness's merge_record and restrict_results", () => {
  const meta = { thresholds: { violence: 0.7, "*": 0.5 } };
  // Python: merge_record recomputes from frames {0: v.7 f.1 (newer), 1: v.2, 2: f.55} → violence 0.7 (>= 0.7), fear 0.55.
  const first = compactResult({ id: "a", status: "ok", flagged: ["violence"], frames: [
    { n: 0, scores: { violence: 0.9 }, asked: ["violence"] }, { n: 1, scores: { violence: 0.2 }, asked: ["violence"] }] }, meta);
  const second = compactResult({ id: "a", status: "ok", flagged: ["violence"], frames: [
    { n: 0, scores: { violence: 0.7, fear: 0.1 }, asked: ["violence", "fear"] }, { n: 2, scores: { fear: 0.55 }, asked: ["fear"] }] },
  meta, new Set([1, 2]));
  const merged = mergeResult(first, second, meta, new Set([1, 2]));
  assert.deepEqual(merged.frames.map((f) => f.n), [0, 1, 2]);
  assert.deepEqual(merged.flagged.sort(), ["fear", "violence"]);
  assert.equal(merged.best.violence, 0);
  assert.ok(Math.abs(merged.scores.violence - 0.7) < 1e-6 && Math.abs(merged.scores.fear - 0.55) < 1e-6);
  assert.deepEqual([...merged.asked].sort(), ["fear", "violence"]);
  // Python restrict_results over frames 1 and 2: violence 0.2 (not flagged), fear 0.55 (flagged).
  assert.deepEqual(merged.sample.flagged, ["fear"]);
  assert.deepEqual(frameTop(merged.frames[0], meta, 1).map(([k]) => k), ["violence"]);
  assert.deepEqual(frameScores(merged.frames[2], meta).map(([k]) => k), ["fear"]);
  assert.equal(second.frames[1].askedKeys, compactResult({ id: "b", frames: [{ n: 0, scores: {}, asked: ["fear"] }] }, meta).frames[0].askedKeys);
});

test("retained size per frame stays compact", () => {
  v8.setFlagsFromString("--expose_gc");
  const gc = vm.runInNewContext("gc");
  const keys = Array.from({ length: 30 }, (_, k) => (k % 5 ? `fam${k % 6}/tok${k}` : `fam${k % 6}`));
  const meta = { thresholds: { "*": 0.5 } };
  const used = () => { gc(); gc(); const m = process.memoryUsage(); return m.heapUsed + m.arrayBuffers; };
  const items = 4000, frames = 8;
  const before = used();
  const kept = [];
  for (let i = 0; i < items; i++) {
    const row = { id: `i${i}`, status: "ok", flagged: [], scores: Object.fromEntries(keys.map((k) => [k, Math.random()])),
      frames: Array.from({ length: frames }, (_, n) => ({ n, scores: Object.fromEntries(keys.map((k) => [k, Math.random()])), asked: [...keys] })) };
    kept.push(compactResult(row, meta));
  }
  const perFrame = (used() - before) / (items * frames);
  // Keeping every score as plain objects retained ~1960 B/frame; packed it is ~740 B/frame.
  assert.ok(perFrame < 1100, `retained ${perFrame.toFixed(0)} B per frame`);
  assert.equal(kept.length, items);
});

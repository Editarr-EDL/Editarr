// Viewer DOM behaviour (landmarks, table headers, focus return) under jsdom from web/node_modules; skipped
// when the web dependencies are not installed.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { test } from "node:test";

let JSDOM = null;
try {
  ({ JSDOM } = createRequire(new URL("../../web/package.json", import.meta.url))("jsdom"));
} catch { /* web dependencies not installed */ }
const skip = JSDOM ? false : "jsdom is not installed (pnpm install in web/)";

const VIEWER = new URL("../../scanner/evaluation/folder_eval/viewer/", import.meta.url);
let mods = null;
async function viewer() {
  if (mods) return mods;
  const dom = new JSDOM('<!doctype html><body><main id="main" tabindex="-1"></main><dialog id="detail"></dialog><div id="status"></div><div id="toast" hidden></div></body>', { url: "http://localhost/_results/index.html" });
  for (const k of ["window", "document", "location", "history", "Node", "Event", "CSS"]) globalThis[k] = dom.window[k];
  globalThis.IntersectionObserver = class { observe() {} unobserve() {} };
  globalThis.localStorage = dom.window.localStorage;
  mods = {
    ui: await import(new URL("ui.js", VIEWER)),
    data: await import(new URL("data.js", VIEWER)),
    metrics: await import(new URL("metrics.js", VIEWER)),
    detail: await import(new URL("detail.js", VIEWER)),
  };
  return mods;
}

test("top and bottom pagers are distinct landmarks; a single page is not one", { skip }, async () => {
  const { ui } = await viewer();
  const top = ui.pager(250, 2, (n) => `#p${n}`, "Pages (top)");
  const bottom = ui.pager(250, 2, (n) => `#p${n}`, "Pages (bottom)");
  assert.equal(top.tagName, "NAV");
  assert.notEqual(top.getAttribute("aria-label"), bottom.getAttribute("aria-label"));
  assert.equal(ui.pager(5, 1, () => "#").tagName, "P");
});

test("film metrics rows are headed by their key, grouped per film", { skip }, async () => {
  const { data, metrics } = await viewer();
  const row = { tp: 1, fp: 0, fn: 0, tn: 1, n: 2 };
  Object.assign(data.S, { familyOrder: ["violence", "fear"], metas: { m: { supports: ["violence", "fear"] } }, summary: { models: { m: {
    families: { violence: row }, tokens: {},
    films: { all: { windows: 4, families: { violence: row, fear: row } }, "films/A": { windows: 4, families: { violence: row, fear: row } } },
  } } } });
  const table = metrics.metricsTable("m", "all").querySelector(".films table");
  const groups = table.querySelectorAll("tbody");
  assert.equal(groups.length, 2);
  assert.ok([...table.querySelectorAll("th")].every((th) => th.textContent.trim()), "no empty header cells");
  assert.equal(groups[1].querySelector('th[scope="rowgroup"]').getAttribute("rowspan"), "2");
  assert.deepEqual([...groups[1].querySelectorAll('th[scope="row"]')].map((th) => th.textContent), ["violence", "fear"]);
});

test("closing the detail returns focus to a link for the item", { skip }, async () => {
  const { detail } = await viewer();
  const main = document.querySelector("#main");
  main.innerHTML = '<a href="#/folder/x?item=abc" tabindex="-1">thumb</a><a id="other" href="#/folder/x?item=abcd">o</a>'
    + '<a id="path" href="#/folder/x?scope=all&item=abc">path</a>';
  detail.restoreDetailFocus("abc");
  assert.equal(document.activeElement.id, "path");
  detail.restoreDetailFocus("missing");
  assert.equal(document.activeElement, main);
});

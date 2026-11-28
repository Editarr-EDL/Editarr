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
  const dom = new JSDOM('<!doctype html><body><input id="filter"><p id="route-status"></p><main id="main" tabindex="-1"></main><dialog id="detail"></dialog><div id="status"></div><div id="toast" hidden></div></body>', { url: "http://localhost/_results/index.html" });
  for (const k of ["window", "document", "location", "history", "Node", "Event", "CSS"]) globalThis[k] = dom.window[k];
  globalThis.IntersectionObserver = class { observe() {} unobserve() {} };
  globalThis.localStorage = dom.window.localStorage;
  mods = {
    ui: await import(new URL("ui.js", VIEWER)),
    data: await import(new URL("data.js", VIEWER)),
    metrics: await import(new URL("metrics.js", VIEWER)),
    detail: await import(new URL("detail.js", VIEWER)),
    narrative: await import(new URL("narrative.js", VIEWER)),
    router: await import(new URL("router.js", VIEWER)),
    media: await import(new URL("media.js", VIEWER)),
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

test("severity chips use each key's scale and compare only to scored expectations", { skip }, async () => {
  const { data, narrative } = await viewer();
  data.S.inv = { severity_max: { violence: 3, sex: 3, nudity: 3, drugs: 2 } };
  const item = { expected_severity: { violence: 2, sex: null, nudity: 0, drugs: 2 }, corr: null };
  const chips = narrative.severityChips({ severity: { violence: 2, drugs: 1, sex: 3 } }, item);
  const texts = [...chips.querySelectorAll(".chip")].map((c) => c.textContent);
  assert.deepEqual(texts, ["drugs 1/2 · exp 2 ≈", "nudity –/3 · exp 0", "sex 3/3", "violence 2/3 · exp 2 ✓"]);
});

test("aria-current is set to a real value and cleared", { skip }, async () => {
  const { ui } = await viewer();
  const link = document.createElement("a");
  ui.setCurrent(link, true);
  assert.equal(link.getAttribute("aria-current"), "page");
  ui.setCurrent(link, true, "true");
  assert.equal(link.getAttribute("aria-current"), "true");
  ui.setCurrent(link, false);
  assert.equal(link.hasAttribute("aria-current"), false);
});

test("route changes set the title, and only page changes announce and move focus", { skip }, async () => {
  const { router } = await viewer();
  const route = (hash) => { location.hash = hash; return router.parseHash(); };
  const filter = document.querySelector("#filter");
  filter.focus();
  assert.equal(router.announceRoute(route("#/model/siglip2?scope=all")), false); // first render
  assert.equal(document.title, "Model siglip2 · Folder evaluation");
  assert.equal(router.announceRoute(route("#/model/siglip2?scope=all&q=fight")), false);
  assert.equal(document.activeElement, filter, "filter-only changes keep focus");
  assert.equal(router.announceRoute(route("#/folder/video/ucf_crime?scope=all")), true);
  assert.equal(document.activeElement, document.querySelector("#main"));
  assert.equal(document.querySelector("#route-status").textContent, "Folder video/ucf_crime");
  assert.equal(document.title, "Folder video/ucf_crime · Folder evaluation");
  filter.focus();
  assert.equal(router.announceRoute(route("#/folder/video?scope=all&item=abc")), false); // detail owns focus
  assert.equal(document.activeElement, filter);
});

function sensitiveSetup(data, { loaded = true } = {}) {
  Object.assign(data.S, { modelIds: ["nudenet"], results: loaded ? { nudenet: new Map([["s", { status: "ok", flagged: ["nudity"] }]]) } : {}, pending: {} });
  document.body.className = "";
  return { id: "s", expected: [], scored: true };
}

function mediaIn(ui, tag) {
  const media = ui.h(tag, { class: "media" });
  ui.h("div", { class: "media-wrap" }, media);
  return media;
}

test("a hidden sensitive video cannot be shown through picture-in-picture or downloads", { skip }, async () => {
  const { ui, data, media } = await viewer();
  const item = sensitiveSetup(data);
  const video = mediaIn(ui, "video");
  media.protect(video, item);
  assert.ok(video.classList.contains("sensitive"));
  assert.ok(video.hasAttribute("disablepictureinpicture") && video.hasAttribute("disableremoteplayback"));
  assert.match(video.getAttribute("controlslist"), /nodownload/);
  video.closest(".media-wrap").querySelector(".reveal").click();
  assert.ok(!video.hasAttribute("disablepictureinpicture") && !video.hasAttribute("controlslist"));
});

test("Open original media respects the reveal state", { skip }, async () => {
  const { ui, data, media } = await viewer();
  const item = sensitiveSetup(data);
  const click = (link) => { const e = new window.MouseEvent("click", { cancelable: true }); link.dispatchEvent(e); return e.defaultPrevented; };

  const img = mediaIn(ui, "img");
  media.protect(img, item);
  const withMedia = ui.h("a", { href: "../x.jpg", target: "_blank" });
  media.guardOriginalLink(withMedia, item, img);
  assert.equal(click(withMedia), true, "hidden: reveal first");
  assert.ok(img.classList.contains("revealed"));
  assert.equal(click(withMedia), false, "revealed: opens");

  const noMedia = ui.h("a", { href: "../x.mkv", target: "_blank", text: "Open original media ↗" });
  media.guardOriginalLink(noMedia, item, null);
  assert.equal(click(noMedia), true);
  assert.match(noMedia.textContent, /click again/);
  assert.equal(click(noMedia), false);

  const safe = ui.h("a", { href: "../y.jpg", target: "_blank" });
  media.guardOriginalLink(safe, { id: "clean", expected: [] }, null);
  assert.equal(click(safe), false);
  document.body.classList.add("show-sensitive");
  const shown = ui.h("a", { href: "../x.jpg", target: "_blank" });
  media.guardOriginalLink(shown, item, null);
  assert.equal(click(shown), false, "the global toggle shows everything");
});

test("content text stays hidden until every model has loaded, and for sensitive items", { skip }, async () => {
  const { ui, data, narrative } = await viewer();
  const clean = { id: "c", expected: [], scored: true };
  sensitiveSetup(data, { loaded: false });
  data.S.loadErrors = {};
  globalThis.fetch = async () => ({ ok: false, status: 500, headers: { get: () => null } });
  const pending = narrative.protectedText(clean, ui.h("span", { text: "FEMALE_BREAST_EXPOSED" }), "detector labels");
  assert.ok(pending.classList.contains("sens-text"), "not yet known: hidden");
  const item = sensitiveSetup(data);
  const sensitive = narrative.protectedText(item, ui.h("span", { text: "label" }), "detector labels");
  assert.ok(sensitive.classList.contains("sens-text"));
  const text = ui.h("span", { text: "label" });
  assert.equal(narrative.protectedText(clean, text, "detector labels"), text, "known and clean: shown");
});

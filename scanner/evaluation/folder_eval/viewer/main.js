/* Viewer entry point (loaded by app.js): routing between the model and folder views. */
import { h, $, fill, setCurrent, setStatus } from "./ui.js";
import { announceRoute, buildHash, parseHash, withParams } from "./router.js";
import { getScope, hasSample } from "./scope.js";
import { S, loadBase } from "./data.js";
import { loadCorrections } from "./corrections.js";
import { initSensitiveToggle } from "./media.js";
import { showModel } from "./model-view.js";
import { showFolder } from "./folder-view.js";
import { initDetail, syncDetail } from "./detail.js";

let routing = Promise.resolve();
function route() { routing = routing.then(doRoute).catch(showError); return routing; }

async function doRoute() {
  const r = parseHash();
  setCurrent($("#nav-model"), r.view !== "folder");
  setCurrent($("#nav-folder"), r.view === "folder");
  syncScopeControl(r);
  if (r.view === "folder") await showFolder(r);
  else await showModel(r);
  announceRoute(parseHash()); // after rendering, which may have resolved the model id
  await syncDetail(r.params.get("item"));
}

/** Header scope switch: reflects the routed (or default) scope and keeps nav links in that scope. */
function syncScopeControl(r) {
  const scope = getScope(r), sel = $("#scope");
  sel.disabled = !hasSample();
  sel.value = scope;
  sel.options[1].textContent = hasSample() ? `Decision sample (${S.subset.ids.size})` : "Decision sample (none)";
  const keep = r.params.has("scope") ? { scope: r.params.get("scope") } : {};
  $("#nav-model").href = buildHash("model", "", keep);
  $("#nav-folder").href = buildHash("folder", "", keep);
}

function showError(err) {
  console.error(err);
  fill($("#main"), h("div", { class: "error", role: "alert" }, h("h2", { text: "Could not load results" }), h("p", { text: String(err.message || err) })));
  setStatus("");
}

window.addEventListener("hashchange", route);
// The skip link targets #main, which is not a route: move focus without changing the hash.
document.querySelector(".skip-link").addEventListener("click", (e) => { e.preventDefault(); $("#main").focus(); });
initSensitiveToggle($("#show-sensitive"));
initDetail();
$("#scope").addEventListener("change", (e) => withParams({ scope: e.target.value, page: "" }));
// Corrections or a refreshed summary changed derived data: rebuild the view, keeping scroll and focus.
window.addEventListener("evaldata", async () => {
  const fkey = document.activeElement?.closest("[data-fkey]")?.dataset.fkey;
  const y = window.scrollY, dlg = $("#detail"), dy = dlg.scrollTop;
  S.shell = "";
  S.folderStats = {};
  await route();
  window.scrollTo(0, y);
  dlg.scrollTop = dy;
  if (fkey) document.querySelector(`[data-fkey="${CSS.escape(fkey)}"]`)?.focus();
});

loadBase().then(loadCorrections).then(route).catch(showError);

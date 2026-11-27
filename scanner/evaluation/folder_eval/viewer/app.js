/* Bootstrap: import modules one at a time in dependency order, because python -m http.server
   resets bursts of parallel connections and a failed module fetch would break the whole viewer. */
const ORDER = ["ui", "results", "data", "scoring", "router", "scope", "corrections", "correction-controls", "media", "narrative", "live-metrics", "metrics", "model-view", "folder-view", "detail", "main"];

try {
  for (const name of ORDER) await import(`./${name}.js`);
} catch (err) {
  console.error(err);
  document.getElementById("main").textContent = `Could not load the viewer (${err.message}). Reload the page to retry.`;
}

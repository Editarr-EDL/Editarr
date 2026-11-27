/* Lazy, throttled image loading and sensitive-content blurring. */
import { loadAllModels } from "./data.js";
import { h, encPath } from "./ui.js";
import { isSensitive } from "./sensitivity.js";

const STORAGE_KEY = "folderEval.showSensitive";
/* python -m http.server has a tiny listen backlog, so images load through a small queue with retries. */
const IMG_CONCURRENCY = 4;

const imgItems = new WeakMap();
const imgQueue = [];
let imgActive = 0;
let ready = null;

/** Wire the global "Show sensitive images" checkbox, persisted in localStorage. */
export function initSensitiveToggle(input) {
  const apply = (on) => document.body.classList.toggle("show-sensitive", on);
  let on = false;
  try { on = localStorage.getItem(STORAGE_KEY) === "1"; } catch { /* storage unavailable */ }
  input.checked = on;
  apply(on);
  input.addEventListener("change", () => {
    apply(input.checked);
    try { localStorage.setItem(STORAGE_KEY, input.checked ? "1" : "0"); } catch { /* ignore */ }
  });
}

const hidden = (el) => el.classList.contains("sensitive") && !el.classList.contains("revealed")
  && !document.body.classList.contains("show-sensitive");

/**
 * Apply the item's current sensitivity to el (idempotent, so reused nodes are rechecked on every render): blur it
 * and add a click-to-reveal button to its .media-wrap, or remove both once the item is no longer sensitive. Only
 * call it after every model's flags have loaded. While el is hidden, a new-tab link around it reveals instead.
 */
export function protect(el, item) {
  const wrap = el.closest(".media-wrap");
  const existing = wrap?.querySelector(".reveal");
  if (!isSensitive(item)) {
    el.classList.remove("sensitive", "revealed");
    existing?.remove();
    return;
  }
  el.classList.add("sensitive");
  if (!wrap || existing) return;
  const btn = h("button", { type: "button", class: "btn reveal", "aria-pressed": "false", text: "Reveal" });
  btn.addEventListener("click", () => {
    const shown = el.classList.toggle("revealed");
    btn.setAttribute("aria-pressed", String(shown));
    btn.textContent = shown ? "Hide" : "Reveal";
  });
  wrap.append(btn);
  const link = el.closest('a[target="_blank"]');
  if (!link || link.dataset.guarded) return;
  link.dataset.guarded = "1";
  link.addEventListener("click", (e) => {
    if (!hidden(el)) return;
    e.preventDefault();
    wrap.querySelector(".reveal")?.click();
  });
}

function pumpImages() {
  while (imgActive < IMG_CONCURRENCY && imgQueue.length) {
    const img = imgQueue.shift();
    if (!img.isConnected) continue;
    imgActive++;
    protect(img, imgItems.get(img));
    const done = (ok) => {
      img.onload = img.onerror = null;
      imgActive--;
      const tries = Number(img.dataset.tries || 0);
      if (!ok && tries < 3) {
        img.dataset.tries = tries + 1;
        setTimeout(() => { imgQueue.push(img); pumpImages(); }, 300 * (tries + 1));
      }
      pumpImages();
    };
    img.onload = () => done(true);
    img.onerror = () => done(false);
    img.src = img.dataset.src + (img.dataset.tries ? `?r=${img.dataset.tries}` : "");
  }
}

// Images wait for every model's flags so a sensitive item is never shown unblurred, even briefly.
const imgObserver = new IntersectionObserver((entries) => {
  for (const e of entries) {
    if (!e.isIntersecting) continue;
    imgObserver.unobserve(e.target);
    imgQueue.push(e.target);
  }
  ready ||= loadAllModels();
  ready.then(pumpImages);
}, { rootMargin: "300px" });

function lazyImg(item, src, attrs) {
  const img = h("img", { ...attrs, "data-src": src, decoding: "async" });
  imgItems.set(img, item);
  imgObserver.observe(img);
  return img;
}

/** Linked frame image (frame n, default first) inside a .media-wrap that can host a reveal button. */
export function frameBlock(item, n, href, { cls = "thumb", alt = "", linkAttrs = {} } = {}) {
  const frames = item.frames || [];
  const fr = frames.find((f) => f.n === n) || frames[0];
  const img = fr ? lazyImg(item, encPath(fr.file), { class: cls, alt }) : h("div", { class: `${cls} none`, text: item.kind || "?" });
  return h("div", { class: "media-wrap" }, h("a", { href, ...linkAttrs }, img));
}

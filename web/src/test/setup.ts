import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";

// Imported lazily so each test file's API mocks apply to the registry loader.
afterEach(async () => { (await import("../shared/mcf-registry")).forgetMcfRegistry(); });

const localValues = new Map<string, string>();
const testLocalStorage: Storage = {
  get length() { return localValues.size; },
  clear() { localValues.clear(); },
  getItem(key) { return localValues.get(key) ?? null; },
  key(index) { return [...localValues.keys()][index] ?? null; },
  removeItem(key) { localValues.delete(key); },
  setItem(key, value) { localValues.set(key, String(value)); },
};
Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: testLocalStorage,
});

// Tests intentionally receive the same explicit root bootstrap as Vite development.
// Production must replace this fixture with its active deployment snapshot.
document.head.insertAdjacentHTML(
  "beforeend",
  '<script id="editarr-bootstrap" type="application/json" data-editarr-bootstrap="development">{"version":1,"basePath":"/","apiBasePath":"/api"}</script>',
);

Object.defineProperty(HTMLMediaElement.prototype, "play", {
  configurable: true,
  value: () => Promise.resolve(),
});

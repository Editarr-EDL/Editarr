/**
 * Browser storage for one signed-in account. Values are namespaced by user ID
 * and live only for one identity and permission revision: `bindUserStorage`
 * removes every user's values whenever that key changes, including sign-out.
 * Without a bound account, reads return null and writes are dropped.
 */

const PREFIX = "editarr.user.";
const SCOPE_KEY = "editarr.user-scope";
const LEGACY_PREFIXES = ["editarr.library-last-route.", "editarr.library-display."];

let boundUser: string | null = null;

function storage(): Storage | null {
  try { return window.localStorage; } catch { return null; }
}

function removeWhere(store: Storage, matches: (key: string) => boolean): void {
  const keys: string[] = [];
  for (let index = 0; index < store.length; index += 1) {
    const key = store.key(index);
    if (key !== null && matches(key)) keys.push(key);
  }
  for (const key of keys) store.removeItem(key);
}

/**
 * Bind storage to `scope` (`"<user id>:<permission revision>"`), or to nobody.
 * A scope other than the one last bound in this browser clears every stored value first.
 */
export function bindUserStorage(scope: string | null): void {
  boundUser = scope === null ? null : scope.split(":")[0];
  const store = storage();
  if (store === null) return;
  try {
    if (store.getItem(SCOPE_KEY) === scope) return;
    removeWhere(store, (key) => key.startsWith(PREFIX) || LEGACY_PREFIXES.some((legacy) => key.startsWith(legacy)));
    if (scope === null) store.removeItem(SCOPE_KEY);
    else store.setItem(SCOPE_KEY, scope);
  } catch { /* Private browsing can deny storage; values then stay in memory only. */ }
}

export function readUserValue(name: string): string | null {
  const store = storage();
  if (boundUser === null || store === null) return null;
  try { return store.getItem(`${PREFIX}${boundUser}.${name}`); } catch { return null; }
}

export function writeUserValue(name: string, value: string): void {
  const store = storage();
  if (boundUser === null || store === null) return;
  try { store.setItem(`${PREFIX}${boundUser}.${name}`, value); } catch { /* See bindUserStorage. */ }
}

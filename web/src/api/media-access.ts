import { runtimeApiBasePath } from "../shared/runtime-urls";

export type MediaTarget = { source: string; itemId: string };
type Watch = { target: MediaTarget; denied: () => void; revalidation?: Promise<boolean> };
const watches = new Set<Watch>();

export function watchMediaAccess(target: MediaTarget, denied: () => void): () => void {
  const watch = { target, denied };
  watches.add(watch);
  return () => { watches.delete(watch); };
}

function bodyTarget(value: unknown): MediaTarget | null {
  if (!value || typeof value !== "object") return null;
  const body = value as Record<string, unknown>;
  if (typeof body.source === "string" && typeof body.item_id === "string") {
    return { source: body.source, itemId: body.item_id };
  }
  if (typeof body.jellyfin_item_id === "string") return { source: "jellyfin", itemId: body.jellyfin_item_id };
  return bodyTarget(body.target) || bodyTarget(body.lookup);
}

function requestTarget(url: URL, init: RequestInit): MediaTarget | null {
  const segments = url.pathname.slice(runtimeApiBasePath().length).split("/").filter(Boolean);
  if (segments[0] === "library-items" && segments[1]) return { source: "jellyfin", itemId: decodeURIComponent(segments[1]) };
  const query = bodyTarget(Object.fromEntries(url.searchParams));
  if (query) return query;
  if (typeof init.body === "string") {
    try { return bodyTarget(JSON.parse(init.body)); } catch { return null; }
  }
  return null;
}

function itemUrl(target: MediaTarget, origin: string): URL | null {
  const item = encodeURIComponent(target.itemId);
  const base = runtimeApiBasePath();
  return target.source === "jellyfin" ? new URL(`${base}/library-items/${item}`, origin) : null;
}

async function refusalCode(response: Response): Promise<string | null> {
  if (response.status !== 403 && response.status !== 404) return null;
  const body = await response.clone().json().catch(() => null);
  const code = body?.detail?.code ?? body?.code;
  return typeof code === "string" && code.length > 0 ? code : null;
}

function deniesMedia(code: string | null, status: number, readingItem: boolean): boolean {
  return code !== null && (
    code === "media_unavailable" || code === "media_denied"
    || (readingItem && (status === 403 || code === "not_found"))
  );
}

function revalidate(watch: Watch, item: URL): Promise<boolean> {
  if (watch.revalidation) return watch.revalidation;
  watch.revalidation = (async () => {
    const fresh = await globalThis.fetch(item, {
      method: "GET", credentials: "same-origin", cache: "no-store", redirect: "error",
      headers: { Accept: "application/json" },
    }).catch(() => null);
    return fresh !== null && deniesMedia(await refusalCode(fresh), fresh.status, true);
  })().finally(() => { delete watch.revalidation; });
  return watch.revalidation;
}

/** Bind denial delivery to the workspace that owned the request when it started. */
export function observeMediaRequest(url: URL, init: RequestInit): (response: Response) => Promise<void> {
  const target = requestTarget(url, init);
  const started = [...watches].filter(watch => !target || (
    watch.target.source === target.source && watch.target.itemId === target.itemId
  ));
  return async (response) => {
    if (!started.some(watch => watches.has(watch))) return;
    const code = await refusalCode(response);
    if (code === null) return;
    const item = target ? itemUrl(target, url.origin) : null;
    const readingItem = (init.method ?? "GET").toUpperCase() === "GET" && (
      item?.pathname === url.pathname || url.pathname === `${runtimeApiBasePath()}/media/audio-tracks`
    );
    const denied = deniesMedia(code, response.status, readingItem);
    for (const watch of started) {
      if (!watches.has(watch)) continue;
      if (!denied && (!item || !await revalidate(watch, item))) continue;
      if (watches.has(watch)) watch.denied();
    }
  };
}
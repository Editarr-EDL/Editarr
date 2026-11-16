import { execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { once } from "node:events";
import http from "node:http";
import path from "node:path";
import { childEnvironment, pythonPath } from "./prerequisites.mjs";

export const LIBRARY = "b".repeat(32);
export const SERVER = "d".repeat(32);
export const FILM = { id: "a".repeat(32), version: "c".repeat(32), title: "Synthetic catalog film" };

/** The catalog hash route of a served item. */
export const itemRoute = item => `#/library/${LIBRARY}/item/${item.id}`;
/** The import and review target of a served item's only version. */
export const jellyfinTarget = item => ({ source: "jellyfin", item_id: item.id, media_source_id: item.version });

/** The app's own probe of a media file's duration, which a real Jellyfin runtime would match. */
export function probedDuration(run, file) {
  return JSON.parse(execFileSync(pythonPath(run.root), ["-B", "-c",
    "import json,sys; from scanner.pipeline.frames import get_video_duration; print(json.dumps(get_video_duration(sys.argv[1])))", file],
  { cwd: run.root, env: childEnvironment(run.root, run.data), encoding: "utf8", timeout: 30000 }));
}

function record(item) {
  const ticks = Math.round(item.durationSeconds * 10_000_000);
  return { Id: item.id, LibraryId: LIBRARY, ParentId: LIBRARY, Name: item.title, Type: "Movie", Path: item.file,
    RunTimeTicks: ticks, Container: "mkv", MediaSourceCount: 1, ProviderIds: { Tmdb: "12345" },
    MediaSources: [{ Id: item.version, Name: "Synthetic version", Path: item.file, Container: "mkv", RunTimeTicks: ticks }], ImageTags: {} };
}

const PROXIED = /^\/(?:Users(?:\/|$)|Sessions\/Logout$|UserImage$)/;

/**
 * Relay a user route to the run's real Jellyfin, swapping this transport's key for
 * Editarr's minted key, so the owner set up there keeps signing in and reauthenticating.
 */
async function proxy(request, response, base, apiKey, realKey) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  const authorization = (request.headers.authorization ?? "").replace(`Token="${apiKey}"`, `Token="${realKey}"`);
  const relayed = await fetch(base + request.url, {
    method: request.method, signal: AbortSignal.timeout(30000), redirect: "manual",
    headers: { Authorization: authorization, Accept: request.headers.accept ?? "application/json", ...(chunks.length ? { "Content-Type": request.headers["content-type"] ?? "application/json" } : {}) },
    ...(chunks.length ? { body: Buffer.concat(chunks) } : {}),
  });
  const body = Buffer.from(await relayed.arrayBuffer());
  response.writeHead(relayed.status, { "Content-Type": relayed.headers.get("content-type") ?? "application/octet-stream" });
  response.end(body);
  return relayed.status;
}

/**
 * Serve a labelled synthetic Jellyfin transport on loopback: one movie library at the
 * media's directory holding FILM and any added items. `media.durationSeconds` is the
 * reported runtime; pass `probedDuration` for a real video so imports fit inside it.
 * `state.unavailable` hides every item read by ID, and `state.itemReads` counts batch
 * reads. Listings page and filter by type like Jellyfin, and every user may open
 * every item through `/Items/{id}`, so Editarr can decide a Jellyfin-linked
 * account's media from its catalog and per-user item reads. Closing it writes each
 * request's method, path, query names, and status to `synthetic-jellyfin-calls.json`. Port 0 lets the caller's listener policy choose. When the run set Editarr
 * up through a real Jellyfin, this transport reports that server's ID and relays its
 * user routes there, so the catalog is synthetic but the owner's link stays valid.
 */
export async function startSyntheticJellyfin(run, media, { port = 0 } = {}) {
  const calls = [];
  const log = [];
  const state = { unavailable: false, itemReads: 0 };
  const items = [{ ...FILM, file: media.file, durationSeconds: media.durationSeconds }];
  const apiKey = randomBytes(24).toString("base64url");
  run.secrets.add(apiKey);
  const real = run.jellyfin;
  const realKey = real ? (await real.editarrKeys())[0] : null;
  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url, "http://provider.test");
    calls.push(url.pathname);
    const answer = status => { log.push({ method: request.method, path: url.pathname, query: [...url.searchParams.keys()].filter(key => key !== "api_key"), status }); };
    if (real && PROXIED.test(url.pathname)) { answer(await proxy(request, response, real.base, apiKey, realKey)); return; }
    let body;
    if (url.pathname === "/Library/VirtualFolders") body = [{ ItemId: LIBRARY, Name: "Synthetic Movies", CollectionType: "movies", Locations: [path.dirname(media.file)] }];
    else if (url.pathname === "/Items") {
      const ids = url.searchParams.get("Ids")?.split(",");
      if (ids) state.itemReads++;
      const types = url.searchParams.get("IncludeItemTypes")?.split(",");
      const listed = ids ? (state.unavailable ? [] : items.filter(item => ids.includes(item.id))) : items.filter(() => !types || types.includes("Movie"));
      const start = Number(url.searchParams.get("StartIndex") ?? 0);
      const limit = Number(url.searchParams.get("Limit") ?? listed.length);
      body = { Items: listed.slice(start, start + limit).map(record), TotalRecordCount: listed.length, StartIndex: start };
    } else if (/^\/Items\/[0-9a-f-]{32,36}$/.test(url.pathname)) {
      const id = url.pathname.slice("/Items/".length).replaceAll("-", "");
      const item = state.unavailable ? null : items.find(entry => entry.id === id);
      if (!item) { answer(404); response.writeHead(404); response.end(); return; }
      body = record(item);
    } else if (url.pathname.startsWith("/System/Info")) body = { Id: real?.serverId ?? SERVER, ServerName: "Synthetic Jellyfin", Version: "12.0.0" };
    else if (url.pathname === "/Users") body = [];
    else { answer(404); response.writeHead(404); response.end(); return; }
    answer(200);
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify(body));
  });
  server.listen(port, "127.0.0.1");
  await once(server, "listening");
  return {
    calls, state, server,
    url: `http://127.0.0.1:${server.address().port}`,
    settings: { server_url: `http://127.0.0.1:${server.address().port}`, api_key: apiKey },
    /** Serve another movie at `file` in the same library, by default with the media's duration. */
    add(item, file, durationSeconds = media.durationSeconds) { const added = { ...item, file, durationSeconds }; items.push(added); return added; },
    close: () => new Promise(resolve => {
      run.write("synthetic-jellyfin-calls.json", log);
      server.close(() => resolve()); server.closeAllConnections();
    }),
  };
}

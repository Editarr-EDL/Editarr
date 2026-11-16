import { vi } from "vitest";
import type { LinkPreview } from "../../api/api-account-link";

export const preview = (overrides: Partial<LinkPreview> = {}): LinkPreview => ({
  user_id: 3, kind: "link",
  identity: { current: null, after: { server_id: "server-a", user_id: "jf-casey" } },
  export_id: { current: "local-3", after: "jf-casey" },
  role: { current: "user", after: "user" },
  media: { current: "local", after: "jellyfin", all_libraries: false, libraries: 2, parental_limit: true },
  profile: { name: { current: "Pat", after: "Casey" }, avatar: "jellyfin", synced_read_only: true },
  proof_basis: "admin_attested",
  status: { current: "disabled", after: "active", cause: "unlinked" },
  exports: [
    { export_id: 11, format: "edl", media_item_id: "movie-1", media_source_id: "src-1", publication_revision: 2, action: "move", conflict: null },
    { export_id: 12, format: "mcf", media_item_id: "movie-1", media_source_id: "src-1", publication_revision: 1, action: "record", conflict: null },
  ],
  blockers: [],
  ...overrides,
});

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

/** A scripted HTTP boundary: each matching request takes the next queued answer. */
export function linkServer() {
  const answers: Record<string, Response[]> = { proof: [], link: [], sync: [] };
  const bodies: { route: string; body: Record<string, unknown> }[] = [];
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const route = url.endsWith("/link/proof") ? "proof" : url.endsWith("/link") ? "link" : url.includes("/jellyfin/sync") ? "sync" : "other";
    if (init?.body) bodies.push({ route, body: JSON.parse(String(init.body)) });
    const next = answers[route]?.shift();
    if (!next) throw new Error(`Unexpected request ${url}`);
    return next;
  });
  vi.stubGlobal("fetch", fetch);
  return {
    bodies,
    answer(route: "proof" | "link" | "sync", status: number, body: unknown) { answers[route].push(json(status, body)); },
    proven(body: Partial<{ proof: string; confirmation: string; preview: LinkPreview }> = {}) {
      answers.proof.push(json(200, { proof: "proof-token", expires_at: "2026-10-27T12:10:00Z", confirmation: "a".repeat(64), preview: preview(), ...body }));
    },
  };
}

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { vi } from "vitest";

import type { ExportStatus } from "../api/api-mcf-types";
import type { Scan } from "../api/api-scan-types";
import type { ReviewFilter, SavedReview } from "../api/api-review-types";
import type { AuthenticationStatus, PrincipalSummary } from "../api/api-system-types";
import { CAPABILITIES, type Capability } from "../auth/capabilities";

/** A signed-in status for `user_id` holding exactly `capabilities` at `revision`. */
export function signedInStatus({
  userId = 1,
  username = "owner",
  role = "owner",
  revision = 1,
  capabilities = CAPABILITIES,
}: {
  userId?: number;
  username?: string | null;
  role?: PrincipalSummary["role"];
  revision?: number;
  capabilities?: readonly Capability[];
} = {}): AuthenticationStatus {
  return {
    state: "authenticated",
    setup_required: false,
    sign_in: { jellyfin: true },
    display_name: username,
    access: {
      version: "editarr-capabilities-9",
      permission_revision: revision,
      principal: {
        user_id: userId, role, credential: "session", status: "active", link_state: "verified",
      },
      capabilities: [...capabilities],
    },
    csrf_token: "A".repeat(43),
    restart_required: false,
  };
}

export const authenticatedStatus = signedInStatus();

export function emptyExports(): ExportStatus {
  return {
    exports: {
      edl: { format: "edl", present: false, exported_at: null, review_id: null, exported_format_revision: null, current: false, outdated: false, exported_scans: [], exported_review: null },
      mcf: { format: "mcf", present: false, exported_at: null, review_id: null, exported_format_revision: null, current: false, outdated: false, exported_scans: [], exported_review: null },
    },
  };
}

export const mediaItem = {
  id: "movie-1",
  library_id: "movies",
  parent_id: null,
  item_type: "Movie",
  name: "Example Movie",
  sort_name: "Example Movie",
  year: 2025,
  overview: "A test movie.",
  path: "/media/movie.mkv",
  official_rating: "PG-13",
  season_number: null,
  index_number: null,
  runtime_ticks: null,
  duration_seconds: 120,
  resolution: "4K",
  media_format: "MKV",
  media_source_count: 1,
  versions: [{
    id: "source-1",
    name: "4K HDR",
    scene_name: "Example.Movie",
    release_group: "GROUP",
    edition: null,
    path: "/media/movie.mkv",
    file_name: "movie.mkv",
    audio_track_count: 1,
    container: "mkv",
    resolution: "4K",
    media_format: "MKV",
    duration_seconds: 120,
    size_bytes: 100,
    exports: emptyExports(),
  }],
  image_tags: {},
  image_url: null,
  media_streams: [],
  media_provider_id: null,
  exports: emptyExports(),
};

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Return a default API mock for component integration tests. */
export function installProductionApi() {
  return vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input), "http://editarr.test");
    if (url.pathname === "/api/auth/status") return json(authenticatedStatus);
    if (url.pathname === "/api/mcf-registry") return json({ families: [
      { token: "language", label: "Language", categories: [{ token: "language", label: "Language (General)" }, { token: "swearing", label: "Swearing" }] },
      { token: "nudity", label: "Nudity", categories: [{ token: "nudity", label: "Nudity (General)" }] },
      { token: "violence", label: "Violence", categories: [{ token: "violence", label: "Violence (General)" }, { token: "weapons", label: "Weapons" }] },
      { token: "dispensable_scenes", label: "Dispensable scenes", categories: [{ token: "dispensable", label: "Dispensable scenes (General)" }] },
    ] });
    if (url.pathname === "/api/settings/jellyfin") return json({ server_url: "http://jellyfin.test", api_key_configured: true, last_checked_at: null, last_error: null });
    if (url.pathname === "/api/settings/models/scans") return json({ audio_padding_seconds: 0.1 });
    if (url.pathname === "/api/settings/filters/personal") return json({
      user_id: 1, mode: "live", mode_locked: false,
      revision: 0, policy_revision: 0, instance_revision: 0,
      rules: [], severities: [], enforced_rules: [], enforced_severities: [],
    });
    if (url.pathname === "/api/libraries") return json([{ id: "movies", name: "Movies", collection_type: "movies", locations: [] }]);
    if (url.pathname === "/api/libraries/movies/items") return json({ items: [mediaItem], total_count: 1, rating_values: [], resolution_values: [] });
    if (url.pathname === "/api/library-items/movie-1" && (!init?.method || init.method === "GET")) return json(mediaItem);
    if (url.pathname === "/api/library-items/movie-1/scans") return json([]);
    if (url.pathname === "/api/scans/queue") return json([]);
    if (url.pathname === "/api/scans/history") return json([]);
    if (url.pathname === "/api/settings/path-mappings/resolve") return json({ local_path: mediaItem.path });
    if (url.pathname === "/api/media/audio-tracks") {
      const track = { stream_index: 1, language: "eng", title: "Main", codec: "aac", channels: 2,
        disposition: 1, start_seconds: 0, duration_seconds: 120, time_base: "1/48000" };
      return json({ file_version: "a".repeat(64), tracks: [track], selection: { file_version: "a".repeat(64), track }, selection_stale: false });
    }
    if (url.pathname === "/api/media/audio-tracks/selection") return json(JSON.parse(String(init?.body)).selection);
    if (url.pathname === "/api/library-items/movie-1/reviews") return json([]);
    if (url.pathname === "/api/media/subtitles/publications") return json({ published_subtitles: [] });
    if (/^\/api\/reviews\/[^/]+\/subtitles\/sources$/.test(url.pathname)) {
      return json({ sources: [], audio_sources: [], audio_source_id: null, fallback: "scan_required", published_subtitles: [] });
    }
    if (url.pathname === "/api/capabilities") return json({ audio_models: [], audio_filters: [], video_models: [], qwen_models: [{ id: "qwen3-vl-8b-instruct-4bit", display_name: "Qwen3-VL 8B", recommended: true }, { id: "qwen3-vl-4b-instruct-4bit", display_name: "Qwen3-VL 4B", recommended: false }], model_capabilities: [] });
    if (url.pathname === "/api/edl-imports/upload" && init?.method === "POST") return json({ review: {}, scan: {}, warning: null }, 201);
    return json({});
  });
}

/** Build a completed scan fixture.
 * @param overrides Scan fields to replace for a scenario.
 */
export function completeScan(overrides: Partial<Scan> = {}): Scan {
  return {
    id: "scan-combined", scan_id: 7, media_item_id: "media-1", jellyfin_item_id: "movie-1",
    is_own: true,
    scan_mode: "content",
    library_id: "movies", item_name: "Example Movie", item_type: "Movie",
    media_source_id: "source-1", media_source_name: "4K HDR", status: "complete",
    scan_audio: true, audio_model: "whisper", audio_filter: "better_profanity",
    scan_video: true, video_model: "nudenet", coarse_scan_interval: null,
    coarse_scan_enabled: false, qwen_model: "qwen3-vl-8b-instruct-4bit", qwen_summaries_per_segment: 3,
    qwen_segment_join_gap_seconds: 0, duration_seconds: 120, video_hash: null, checkpoint_stage: "complete",
    error_message: null, finding_count: 2, manual: false, edited: false,
    scan_started_at: "2026-09-14T10:00:00Z", scan_duration_seconds: 30,
    created_at: "2026-09-14T10:00:00Z", updated_at: "2026-09-14T10:00:30Z",
    name: "Combined scan", origin: "generated", import_source_name: null, imported_at: null,
    mcf_document: null, credits_detection: null,
    ...overrides,
  };
}

export function savedReview(overrides: Partial<SavedReview> = {}): SavedReview {
  return {
    id: "review-1", media_item_id: "movie-1",
    repair: null,
    target: { source: "jellyfin", item_id: "movie-1", media_source_id: "source-1" },
    name: "Existing review", revision: 1, content_revision: 1, edl_content_revision: 1, mcf_content_revision: 1,
    created_at: "2026-09-14T10:00:00Z", updated_at: "2026-09-14T10:00:00Z",
    exports: emptyExports(), memberships: [], scans: [], filters: [],
    mcf_document: { title: "Example Movie", year: 2025, work_type: "movie", season: null, episode: null, source_url: null, imdb_url: null, release: null, comment: null, material_start_seconds: 0, material_end_seconds: 120 },
    synchronization: { start_seconds: 0, end_seconds: 120, end_mode: "before_credits" },
    accepted_credits: null,
    ...overrides,
  };
}

export function reviewFilter(id: string, source: "audio" | "visual"): ReviewFilter {
  return {
    id, source_scan_id: "scan-combined", source_finding_id: source === "audio" ? 11 : 12,
    source, category: source === "audio" ? "language" : "violence",
    subcategory: source === "audio" ? "swearing" : "violence",
    severity: "medium", mcf_channel: source === "audio" ? "audio" : "video", mcf_comment: null,
    label: source === "audio" ? "Spoken word" : "Fight",
    details: source === "audio" ? "Spoken word in dialogue" : "Fight in hallway",
    group_key: source === "audio" ? null : "violence", start_seconds: source === "audio" ? 5 : 20,
    end_seconds: source === "audio" ? 6 : 25, confidence: 0.9,
    edit_action: source === "audio" ? "mute" : "skip", selected: true,
  };
}

export async function openFirstReviewFindingGroup(section: HTMLElement): Promise<void> {
  const sectionName = section.getAttribute("aria-label");
  if (!sectionName) throw new Error("Expected a named review section");
  const subcategory = within(section).getAllByRole("button").find((button) => button.getAttribute("aria-label")?.includes("subcategory,"));
  if (!subcategory) throw new Error("Expected a review subcategory disclosure");
  if (subcategory.getAttribute("aria-expanded") === "false") fireEvent.click(subcategory);
  const group = await waitFor(() => {
    const currentSection = screen.getByRole("region", { name: sectionName });
    const match = within(currentSection).getAllByRole("button").find((button) => button.getAttribute("aria-label")?.includes("finding group,"));
    if (!match) throw new Error("Expected a review finding-group disclosure");
    return match;
  });
  if (group.getAttribute("aria-expanded") === "false") fireEvent.click(group);
}

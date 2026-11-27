import type { Page } from "@playwright/test";
import { emptyExports, items, json, mockReview, type MockReview, type MockReviewFilter } from "./app-fixtures";

/**
 * Capture-only API data for the website screenshots: synthetic "Example Movie" scans, findings, model choices,
 * accounts and queue state. Register after `mockApi` so these routes take priority.
 */

const at = "2026-11-21T19:30:00Z";
const scanBase = {
  library_id: "movies", jellyfin_item_id: "movie-1", media_item_id: "media-1", item_name: "Example Movie", item_type: "Movie",
  media_source_id: "source-1", media_source_name: "4K HDR", is_own: true, scan_mode: "content", status: "complete",
  origin: "generated", error_message: null, coarse_scan_enabled: false, coarse_scan_interval: null,
  qwen_model: "qwen3-vl-8b-instruct-4bit", qwen_summaries_per_segment: 3, qwen_segment_join_gap_seconds: 2,
  duration_seconds: 5700, video_hash: null, checkpoint_stage: "complete", manual: false, edited: false,
  import_source_name: null, imported_at: null, mcf_document: null, credits_detection: null, frame_interval: 1, speed_up: false,
  release_version: "1.6.0", scanner_behavior_version: "2026.11",
  scan_audio: false, audio_model: null, audio_filter: null, scan_video: false, video_model: null,
  created_at: at, updated_at: at, scan_started_at: at,
};
const track = { stream_index: 1, language: "eng", title: "English 5.1", codec: "eac3", channels: 6,
  disposition: 1, start_seconds: 0, duration_seconds: 5700, time_base: "1/48000" };
const commentary = { ...track, stream_index: 2, title: "Director commentary", codec: "aac", channels: 2, disposition: 0 };
/** A complete stage of a scan, as the scans list summarizes it. */
const coverage = (stage: string) => ({ stage, execution_id: `${stage}-1`, status: "complete", source: "generated", release_version: "1.6.0",
  scanner_behavior_version: "2026.11", build_revision: null, model_id: null, model_revision: null, model_digest: null, quantization: null,
  effective_settings: null, intervals: null, sampling_seconds: null });
const selection = { file_version: "a".repeat(64), track };

export const scans = [
  { ...scanBase, id: "scan-dialogue", scan_id: 4, name: "Dialogue", scan_audio: true, audio_model: "whisper", audio_filter: "qwen",
    analyzed_audio_selection: selection, finding_count: 9, scan_duration_seconds: 742, stage_coverage: [coverage("audio")] },
  { ...scanBase, id: "scan-picture", scan_id: 5, name: "Picture", scan_video: true, video_model: "qwen3-vl-8b",
    finding_count: 5, scan_duration_seconds: 2315, stage_coverage: [coverage("video")] },
];

type Finding = Omit<MockReviewFilter, "id" | "source_scan_id" | "source_finding_id">;
const word = (start: number, label: string, subcategory: string, severity: Finding["severity"]): Finding => ({
  source: "audio", category: "language", subcategory, severity, mcf_channel: "audio", mcf_comment: null, label, details: null,
  group_key: null, start_seconds: start, end_seconds: start + 0.6, confidence: 0.94, edit_action: "mute", selected: true,
});
const scene = (start: number, length: number, category: string, subcategory: string, severity: Finding["severity"], label: string): Finding => ({
  source: "visual", category: category as Finding["category"], subcategory, severity, mcf_channel: "video", mcf_comment: null,
  label, details: label, group_key: null, start_seconds: start, end_seconds: start + length, confidence: 0.88,
  edit_action: "skip", selected: severity !== "low",
});
const findings: Record<string, Finding[]> = {
  "scan-dialogue": [
    word(312.4, "damn", "swearing", "medium"), word(845.1, "hell", "swearing", "low"), word(1290.2, "oh my God", "blasphemy", "medium"),
    word(1733.8, "idiot", "nameCalling", "low"), word(2410.5, "crap", "vulgarity", "low"), word(3015.3, "damn it", "swearing", "medium"),
    word(3888.9, "jerk", "nameCalling", "low"), word(4520.7, "hell", "swearing", "low"), word(5120.6, "God", "blasphemy", "medium"),
  ],
  "scan-picture": [
    scene(1502, 9, "violence", "punching", "high", "Two characters trade punches in a parking garage"),
    scene(2205, 14, "violence", "weapons", "medium", "A guard draws a handgun during a standoff"),
    scene(2870, 6, "sex", "kissing", "low", "A couple kisses on a balcony at night"),
    scene(3340, 8, "nudity", "bareButtocks", "medium", "Brief rear nudity while a character changes"),
    scene(4102, 11, "violence", "punching", "medium", "A bar fight breaks out after an argument"),
  ],
};
/** Every finding of `scanId` with its server ids. */
export function scanFindings(scanId: string) {
  return (findings[scanId] ?? []).map((finding, index) => ({ ...finding, id: index + (scanId === "scan-dialogue" ? 100 : 200), scan_id: scanId }));
}

const registry = { families: [
  { token: "language", label: "Language", categories: [{ token: "swearing", label: "Swearing" }, { token: "blasphemy", label: "Blasphemy" },
    { token: "nameCalling", label: "Name-calling" }, { token: "vulgarity", label: "Vulgarity" }] },
  { token: "violence", label: "Violence", categories: [{ token: "violence", label: "Violence (General)" },
    { token: "punching", label: "Punching" }, { token: "weapons", label: "Weapons" }] },
  { token: "nudity", label: "Nudity", categories: [{ token: "nudity", label: "Nudity (General)" }, { token: "bareButtocks", label: "Bare buttocks" }] },
  { token: "sex", label: "Sex", categories: [{ token: "kissing", label: "Kissing" }] },
] };

const choice = (id: string, display_name: string, recommended = false) => ({ id, display_name, recommended });
const ready = (bytes: number | null) => ({ state: bytes === null ? "not_required" : "ready", bytes_downloaded: bytes, bytes_total: bytes, error_code: null, error_detail: null });
const capability = (id: string, display_name: string, stage: string, device: string, bytes: number | null, extra = {}) => ({
  ...choice(id, display_name), stage, availability: "available", reason: null, device,
  weight_requirement: bytes === null ? "none" : "on_demand", download: ready(bytes), enrichment_behavior: "none", ...extra,
});
const modelCapabilities = [
  capability("whisper", "Whisper (faster-whisper)", "audio", "cpu", 147_882_941),
  capability("apple_speech", "Apple Speech", "audio", "none", null),
  capability("better_profanity", "better-profanity", "audio", "cpu", null),
  capability("qwen", "Qwen (multilingual)", "audio", "mlx", 5_776_630_909),
  capability("qwen3-vl-8b", "Qwen3-VL 8B", "detector", "mlx", 5_776_630_909, { recommended: true }),
  capability("qwen3-vl-4b", "Qwen3-VL 4B", "detector", "mlx", 3_109_729_929),
  capability("d1-3b", "d1-3B", "detector", "metal", 6_265_040_453),
  capability("nudenet", "NudeNet", "detector", "coreml", null),
  capability("qwen3_vl", "Qwen3-VL", "enrichment", "mlx", 5_776_630_909, { enrichment_behavior: "qwen_summary" }),
];
const capabilities = {
  audio_models: [choice("whisper", "Whisper (faster-whisper)"), choice("apple_speech", "Apple Speech")],
  audio_filters: [choice("better_profanity", "better-profanity"), choice("glin_profanity", "glin-profanity"), choice("qwen", "Qwen (multilingual)")],
  video_models: [choice("qwen3-vl-8b", "Qwen3-VL 8B", true), choice("qwen3-vl-4b", "Qwen3-VL 4B"), choice("d1-3b", "d1-3B"), choice("nudenet", "NudeNet")],
  qwen_models: [choice("qwen3-vl-8b-instruct-4bit", "Qwen3-VL 8B", true), choice("qwen3-vl-4b-instruct-4bit", "Qwen3-VL 4B")],
  target_id: "macos-arm64", model_capabilities: modelCapabilities,
};

const queueScan = (scanId: number, name: string, extra: Record<string, unknown>) => ({
  ...scanBase, id: `queue-${scanId}`, scan_id: scanId, item_name: name, jellyfin_item_id: `queue-item-${scanId}`, media_item_id: `queue-media-${scanId}`,
  media_source_name: "1080p", status: "queued", queue_id: scanId, sort_order: scanId, progress_percent: null, progress_stage: null,
  progress_message: null, scan_audio: true, audio_model: "whisper", audio_filter: "qwen", scan_video: true, video_model: "qwen3-vl-8b",
  scan_started_at: null, scan_duration_seconds: null, finding_count: 0, ...extra,
});
const queue = [
  queueScan(11, "Example Movie", { status: "scanning", media_source_name: "4K HDR", progress_percent: 62, progress_stage: "video",
    progress_message: "Describing flagged scenes", scan_started_at: at, scan_duration_seconds: 1484 }),
  queueScan(12, "Example Series · S1E3", { item_type: "Episode" }),
  queueScan(13, "Example Series · S1E4", { item_type: "Episode" }),
  queueScan(14, "Harbor Lights", { scan_video: false, video_model: null }),
];
const history = [
  ...scans,
  { ...scanBase, id: "history-21", scan_id: 3, jellyfin_item_id: "episode-2", media_item_id: "media-e2", item_name: "Example Series · S1E2", item_type: "Episode", media_source_name: "1080p",
    scan_audio: true, audio_model: "apple_speech", audio_filter: "better_profanity", finding_count: 4, scan_duration_seconds: 388 },
  { ...scanBase, id: "history-22", scan_id: 2, jellyfin_item_id: "episode-1", media_item_id: "media-e1", item_name: "Example Series · S1E1", item_type: "Episode", media_source_name: "1080p",
    scan_audio: true, audio_model: "whisper", audio_filter: "qwen", scan_video: true, video_model: "d1-3b", finding_count: 7, scan_duration_seconds: 1720 },
  { ...scanBase, id: "history-23", scan_id: 1, jellyfin_item_id: "silver-tide", media_item_id: "media-st", item_name: "Silver Tide", media_source_name: "4K", status: "cancelled",
    scan_video: true, video_model: "nudenet", finding_count: 0, scan_duration_seconds: 96 },
];

/** A flat-color poster drawn as SVG, so no external artwork is needed and screenshots compress well. */
export function posterSvg(title: string, sky = "#1d3b72", hills = "#7a2e5b") {
  const text = title.replace(/[&<>"']/g, (character) => `&#${character.charCodeAt(0)};`);
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 300"><rect width="200" height="300" fill="${sky}"/><circle cx="148" cy="72" r="32" fill="#ffffff" fill-opacity="0.22"/><path d="M0 200 L55 140 L100 185 L150 120 L200 175 L200 300 L0 300Z" fill="${hills}"/><path d="M0 245 L70 200 L130 235 L200 205 L200 300 L0 300Z" fill="#000" fill-opacity="0.3"/><text x="16" y="276" fill="#fff" font-family="sans-serif" font-weight="800" font-size="22">${text}</text></svg>`;
}

/** Owner fields the server adds to a saved review read by its owner. */
const owned = (review: MockReview) => ({ ...review, owner_id: 1, original_author_id: 1, policy_owner_id: 1, policy_revision: 1, repair: null });

/** Serve reviews from an in-memory store, starting with `initial`, and return the store. */
async function mockReviews(page: Page, initial: MockReview[]) {
  const store = { reviews: initial };
  let next = 50;
  await page.route(/\/api\/library-items\/movie-1\/reviews(\?.*)?$/, async (route) => {
    if (route.request().method() === "GET") return json(route, store.reviews.map(owned));
    const payload = route.request().postDataJSON() as { name: string; media_source_id: string | null; memberships: MockReview["memberships"]; filters: MockReviewFilter[] };
    const created = mockReview(`review-${next++}`, payload.name, payload.media_source_id ?? "source-1", payload.filters ?? []);
    created.memberships = payload.memberships ?? [];
    created.created_at = created.updated_at = "2026-11-22T20:15:00Z";
    store.reviews = [created, ...store.reviews];
    return json(route, owned(created), 201);
  });
  await page.route(/\/api\/reviews\/review-[^/]+$/, async (route) => {
    const id = new URL(route.request().url()).pathname.split("/").at(-1);
    const review = store.reviews.find((candidate) => candidate.id === id)!;
    if (route.request().method() === "PUT") {
      const payload = route.request().postDataJSON() as { name: string; memberships: MockReview["memberships"]; filters: MockReviewFilter[] };
      Object.assign(review, { name: payload.name, memberships: payload.memberships, filters: payload.filters,
        revision: review.revision + 1, content_revision: review.content_revision + 1, updated_at: "2026-11-22T20:15:00Z" });
    }
    return json(route, owned(review));
  });
  return store;
}

/** A saved "Movie night" review of both example scans, as the Subtitles and Publish shots start from. */
export function movieNight() {
  const filters = ["scan-dialogue", "scan-picture"].flatMap((scanId) => scanFindings(scanId).map(({ id, scan_id, ...finding }) => ({
    ...finding, id: `filter-${id}`, source_scan_id: scan_id, source_finding_id: id,
  })));
  const review = mockReview("review-1", "Movie night", "source-1", filters);
  review.memberships = [{ scan_id: "scan-dialogue", role: "audio" }, { scan_id: "scan-picture", role: "video" }];
  review.revision = 3;
  review.updated_at = "2026-11-22T20:15:00Z";
  return review;
}

/** Register the capture data shared by every screen. Pass `reviews` to start with saved reviews. */
export async function mockWebsiteData(page: Page, { reviews = [] as MockReview[] } = {}) {
  await page.route("**/api/mcf-registry", (route) => json(route, registry));
  await page.route("**/api/capabilities", (route) => json(route, capabilities));
  await page.route("**/api/media-fingerprints/queue", (route) => json(route, []));
  await page.route("**/api/library-items/movie-1/scans", (route) => json(route, scans));
  await page.route(/\/api\/scans\/history(\?.*)?$/, (route) => json(route, history));
  // The running scan reports fresh progress, so it reads as active rather than stalled.
  await page.route("**/api/scans/queue", (route) => json(route, queue.map((scan) => scan.status === "scanning"
    ? { ...scan, updated_at: new Date().toISOString(), scan_duration_as_of: new Date().toISOString() } : scan)));
  await page.route(/\/api\/library-items\/movie-1\/exports(\?.*)?$/, (route) => json(route, {
    exports: emptyExports().exports, legacy_exports: emptyExports().exports, publications: [] }));
  await page.route(/\/api\/scans\/[^/]+\/findings$/, (route) => {
    const scanId = new URL(route.request().url()).pathname.split("/")[3];
    return json(route, { revision: 0, findings: scanFindings(scanId) });
  });
  await page.route(/\/api\/media\/audio-tracks(\?.*)?$/, (route) => json(route, {
    file_version: selection.file_version, tracks: [track, commentary], selection, selection_stale: false }));
  await page.route("**/api/media/audio-tracks/selection", (route) => json(route, route.request().postDataJSON().selection));
  await page.route("**/api/library-items/movie-1/image", (route) => route.fulfill({ contentType: "image/svg+xml", body: posterSvg("Example Movie") }));
  await page.route("**/api/library-items/movie-1", (route) => json(route, { ...items[0], exports: emptyExports(),
    versions: items[0].versions.map((version) => ({ ...version, exports: emptyExports() })) }));
  return mockReviews(page, reviews);
}

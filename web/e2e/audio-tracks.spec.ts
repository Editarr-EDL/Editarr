import { chooseWorkflowStep } from "./workflow-actions";
import { expect, test } from "@playwright/test";
import type { AudioTrack, AudioTrackSelection } from "../src/api/api-audio-tracks";
import { expectNoAccessibilityViolations, expectNoHorizontalPageOverflow, json, mockApi } from "./app-fixtures";

const tracks: AudioTrack[] = ["Main", "Commentary"].map((title, position) => ({
  stream_index: position + 1, language: "eng", title, codec: "aac", channels: 2,
  disposition: 0, start_seconds: 0, duration_seconds: 120, time_base: "1/48000",
}));

for (const width of [320, 1440]) {
  test(`${width}px Jellyfin item scans use exact selected tracks`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page);
    await page.route(/\/api\/capabilities$/, (route) => json(route, {
      audio_models: [{ id: "whisper", display_name: "Whisper" }],
      audio_filters: [
        { id: "better_profanity", display_name: "better-profanity" },
        { id: "qwen", display_name: "Qwen (multilingual)" },
      ],
      video_models: [{ id: "nudenet", display_name: "NudeNet", recommended: false }, { id: "qwen3-vl-8b", display_name: "Qwen3-VL 8B", recommended: true }],
      qwen_models: [
        { id: "qwen3-vl-4b-instruct-4bit", display_name: "Qwen3-VL 4B", recommended: false },
        { id: "qwen3-vl-8b-instruct-4bit", display_name: "Qwen3-VL 8B", recommended: true },
      ],
      model_capabilities: [],
    }));
    const memory = new Map<string, AudioTrackSelection>();
    const submissions: Record<string, any>[] = [];
    const selections: Record<string, unknown>[] = [];
    let probe: "tracks" | "empty" | "unknown" | "failure" = "tracks";
    await page.route(/\/api\/scans\/history$/, (route) => {
      const scan = { id: "scan-previous", scan_id: 2, media_item_id: "media-1", jellyfin_item_id: "movie-1", library_id: "movies",
        item_name: "Example Movie", item_type: "Movie", media_source_id: "source-1", media_source_name: "4K HDR",
        status: "complete", scan_mode: "content", scan_audio: true, audio_model: "whisper", audio_filter: "better_profanity",
        scan_video: false, speed_up: false, error_message: null, origin: "generated",
        created_at: "2026-08-19T00:00:00Z", updated_at: "2026-08-19T00:00:00Z" };
      return json(route, [scan, { ...scan, id: "actual", scan_id: 3,
        analyzed_audio_selection: { file_version: "a".repeat(64), track: tracks[1] } }]);
    });
    await page.route(/\/api\/media\/audio-tracks(?:\/selection)?(?:\?|$)/, async (route) => {
      if (route.request().method() === "POST") {
        expect(route.request().headers()["x-csrf-token"]).toBe("test-csrf-token");
        const request = route.request().postDataJSON();
        selections.push(request);
        memory.set(`${request.item_id}:${request.media_source_id}`, request.selection);
        await json(route, request.selection);
        return;
      }
      const query = new URL(route.request().url()).searchParams;
      expect([...query.keys()].sort()).toEqual(["item_id", "media_source_id"]);
      if (probe === "failure") { probe = "tracks"; await json(route, { detail: "Probe unavailable" }, 503); return; }
      const observed = probe === "empty" ? [] : probe === "unknown" ? [{ ...tracks[0], language: null, title: null }] : tracks;
      const version = "a".repeat(64);
      const remembered = memory.get(`${query.get("item_id")}:${query.get("media_source_id")}`);
      const stale = probe === "tracks" && !!remembered && remembered.file_version !== version;
      await json(route, { file_version: version, tracks: observed,
        selection: probe === "tracks" ? (stale ? null : remembered || null) : observed.length === 1 ? { file_version: version, track: observed[0] } : null,
        selection_stale: stale });
    });
    await page.route(/\/api\/scans$/, async (route) => {
      if (route.request().method() !== "POST") { await json(route, []); return; }
      const request = route.request().postDataJSON();
      submissions.push(request);
      await json(route, { id: "selected", scan_id: 9, status: "queued", ...request }, 201);
    });
    await page.goto("/#/library/movies/item/movie-1");
    await chooseWorkflowStep(page, "Scans");
    await expect(page.getByText(/Analyzed audio: Stream 2.*Commentary/)).toBeVisible();
    const ordinaryScan = page.getByRole("listitem").filter({ hasText: /Scan #2.*complete/ });
    await expect(ordinaryScan.getByText(/Analyzed audio track unknown/)).toBeVisible();
    await page.getByRole("button", { name: "New Scan", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "New Scan" });
    const catalogSelect = dialog.getByLabel("Audio track", { exact: true });
    await expect(catalogSelect.locator("option").filter({ hasText: /Stream 2.*eng.*Commentary/ })).toBeAttached();
    await expect(dialog.getByRole("button", { name: "Add to Queue" })).toBeDisabled();
    await catalogSelect.focus();
    await expect(catalogSelect).toBeFocused();
    await catalogSelect.press("Tab");
    await expect(dialog.getByLabel("Transcriber", { exact: true })).toBeFocused();
    await catalogSelect.selectOption("2");
    await expect(catalogSelect).toHaveValue("2");
    await expect(dialog.getByText("Stream 2 / eng / Commentary / aac / 2 channels", { exact: true }).filter({ visible: true })).toBeVisible();
    const catalogFilter = dialog.getByLabel("Filter", { exact: true });
    await expect(catalogFilter).toHaveValue("better_profanity");
    await expect(catalogFilter.getByRole("option", { name: "Qwen (multilingual)" })).toBeAttached();
    await catalogFilter.selectOption("qwen");
    await expect(catalogFilter).toHaveValue("qwen");
    const severityModel = dialog.getByRole("combobox", { name: "Severity and Description model" });
    await expect(severityModel).toHaveValue("qwen3-vl-8b-instruct-4bit");
    await expect(severityModel.getByRole("option", { name: "Qwen3-VL 8B (recommended)" })).toBeAttached();
    await expect(severityModel).toHaveAccessibleDescription("Describes flagged scenes and rates their severity when the video model doesn't.");
    await expect(dialog.getByRole("button", { name: "Add to Queue" })).toBeEnabled();
    await expectNoAccessibilityViolations(page); await expectNoHorizontalPageOverflow(page);
    await page.screenshot({ path: `test-results/css35c-catalog-${width}.png`, fullPage: true });
    await dialog.getByRole("button", { name: "Add to Queue" }).click();
    await expect(dialog).not.toBeVisible();
    expect(submissions[0].audio_selection.track).toEqual(tracks[1]);
    expect(submissions[0].audio_selection.file_version).toBe("a".repeat(64));
    expect(submissions[0].audio_filter).toBe("qwen");
    expect(submissions[0].qwen_model).toBe("qwen3-vl-8b-instruct-4bit");
    expect(selections.at(-1)).toEqual({ item_id: "movie-1", media_source_id: "source-1", selection: submissions[0].audio_selection });

    const reopen = async () => {
      await page.reload();
      await chooseWorkflowStep(page, "Scans");
      await page.getByRole("button", { name: "New Scan", exact: true }).click();
      await expect(dialog).toBeVisible();
    };
    await reopen();
    await expect(catalogSelect).toHaveValue("2");
    memory.set("movie-1:source-1", { file_version: "b".repeat(64), track: tracks[1] });
    await reopen();
    await expect(dialog.getByRole("alert")).toHaveText("Audio selection changed. Choose a track again.");
    await expect(catalogSelect).toHaveValue("");
    await expect(dialog.getByRole("button", { name: "Add to Queue" })).toBeDisabled();
    await catalogSelect.selectOption("2");
    await dialog.getByRole("button", { name: "Add to Queue" }).click();
    await expect(dialog).not.toBeVisible();
    expect(submissions[1].audio_selection).toEqual({ file_version: "a".repeat(64), track: tracks[1] });

    probe = "empty"; await reopen();
    await expect(dialog.getByRole("status").filter({ hasText: "No audio tracks" })).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Add to Queue" })).toBeDisabled();
    await dialog.getByLabel("Scan type").selectOption("video");
    await expect(dialog.getByRole("button", { name: "Add to Queue" })).toBeEnabled();
    probe = "unknown"; await reopen();
    await expect(catalogSelect.locator("option").filter({ hasText: /Unknown language.*Untitled/ })).toBeAttached();
    probe = "failure"; await reopen();
    await expect(dialog.getByRole("alert").filter({ hasText: "Probe unavailable" })).toBeVisible();
    await dialog.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(catalogSelect.locator("option").filter({ hasText: /Commentary/ })).toBeAttached();
    await expectNoAccessibilityViolations(page); await expectNoHorizontalPageOverflow(page);
  });
}

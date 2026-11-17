import { expect, test, type Page } from "@playwright/test";
import { expectNoAccessibilityViolations, expectNoHorizontalPageOverflow, json, mockApi } from "./app-fixtures";
import { communityResponse, mixedCandidate } from "../src/test/community-lookup-fixtures";

const second = { ...mixedCandidate, contributionId: "second-root", revisionId: "second", artifactDigest: "b".repeat(64),
  summary: { ...mixedCandidate.summary, submission_id: "second", name: "Second scan", channels: { video: 2 } } };
const track = { stream_index: 1, language: "eng", title: "Main", codec: "aac", channels: 2, disposition: 1,
  start_seconds: 0, duration_seconds: 120, time_base: "1/48000" };
const selection = { file_version: "f".repeat(64), track };
const savedScan = (revisionId: string, channels: string[]) => ({
  id: `saved-${revisionId}`, scan_id: revisionId === "mixed" ? 71 : 72, name: revisionId === "mixed" ? "Community mixed" : "Community second",
  origin: "imported", import_source_name: null, imported_at: "2026-10-04T00:00:00Z", media_item_id: "local-target",
  jellyfin_item_id: "movie-1", library_id: "movies", item_name: "Example Movie", item_type: "Movie", media_source_id: "source-2",
  media_source_name: "1080p SDR", scan_mode: "content", status: "complete", scan_audio: true, audio_model: null, audio_filter: null,
  scan_video: true, video_model: null, coarse_scan_enabled: false, coarse_scan_interval: null, qwen_model: "unknown",
  qwen_summaries_per_segment: 4, qwen_segment_join_gap_seconds: 0, duration_seconds: 5700, finding_count: 1, error_message: null,
  mcf_document: null, credits_detection: null, created_at: "2026-10-04T00:00:00Z", updated_at: "2026-10-04T00:00:00Z",
  community_provenance: { author_id: "signed-author", contribution_id: revisionId === "mixed" ? "mixed-root" : "second-root", revision_id: revisionId, digest: "a".repeat(64) },
  community_approvals: [{ source: "jellyfin", target_item_id: "movie-1", media_source_id: "source-2", channels }],
});

async function setup(page: Page) {
  await mockApi(page);
  const committed = new Map<string, string[]>();
  const downloads: { choice: { revisionId: string; channels: string[] }; confirmed: boolean }[] = [];
  let reviewWrites = 0;
  const savedMemberships: { scan_id: string; role: string }[][] = [];
  let exportWrites = 0;
  page.on("request", (request) => {
    if (request.method() !== "GET" && /\/reviews(?:\/|\?|$)/.test(request.url())) {
      reviewWrites++;
      const body = request.postDataJSON();
      if (body?.memberships) savedMemberships.push(body.memberships);
    }
    if (request.method() !== "GET" && /\/exports|\/edl/.test(request.url())) exportWrites++;
  });
  await page.route("**/api/filter-candidates", (route) => json(route, { media_item_id: "local-target", matches: [] }));
  await page.route("**/api/media/audio-tracks?*", (route) => json(route, { file_version: selection.file_version, tracks: [track], selection, selection_stale: false }));
  await page.route("**/api/community/lookup", (route) => json(route, communityResponse([mixedCandidate, second])));
  await page.route("**/api/scans/history", (route) => json(route, [...committed].map(([id, channels]) => savedScan(id, channels))));
  await page.route(/\/api\/scans\/saved-[^/]+\/findings$/, (route) => json(route, { revision: 0, findings: [{
    id: 1, scan_id: "saved-mixed", source: "visual", category: "nudity", subcategory: "nudity", severity: "low", mcf_channel: "video",
    mcf_comment: null, start_seconds: 10, end_seconds: 12, confidence: 0.8, label: "Video cue", details: null, group_key: null, selected: true, edit_action: "skip",
  }] }));
  await page.route("**/api/community/downloads/status", (route) => {
    expect(route.request().headers()["x-csrf-token"]).toBe("test-csrf-token");
    return json(route, { version: 1, outcomes: route.request().postDataJSON().choices.map((choice: { revisionId: string; channels: string[] }) => {
      const channels = committed.get(choice.revisionId);
      return channels ? { revisionId: choice.revisionId, status: choice.channels.every((channel) => channels.includes(channel)) ? "imported" : "partial",
        scanId: `saved-${choice.revisionId}`, approvedChannels: channels, receiptState: "pending", reviewRequired: channels.includes("audio"), reviewed: reviewWrites > 0 }
        : { revisionId: choice.revisionId, status: "not_imported" };
    }) });
  });
  await page.goto("/#/library/movies");
  await page.getByRole("button", { name: "Open Example Movie" }).click();
  await page.getByLabel("Review version").selectOption("source-2");
  const open = async () => {
    await page.getByRole("button", { name: "Find Filters", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Found Filters" });
    await expect(dialog.getByLabel("Audio track")).toHaveValue("1");
    await dialog.getByRole("button", { name: "Find community filters" }).click();
    await expect(dialog.getByRole("table", { name: "Community filter matches" }).getByRole("row")).toHaveCount(3);
    await expect(dialog.getByText("Checking committed imports...")).toHaveCount(0);
    return dialog;
  };
  return { committed, downloads, open, savedMemberships, counts: () => ({ reviewWrites, exportWrites }) };
}

for (const width of [320, 1440]) {
  test(`${width}px feedback after download and reuse preserves exact pending intent and private fresh state`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const state = await setup(page);
    const intents = new Map<string, { vote: string; state: string }>();
    const sends: { operation_id: string; vote?: string }[] = [];
    let currentVote = "neutral", loseReply = true;
    await page.route("**/api/community/downloads", (route) => {
      const body = route.request().postDataJSON(); state.downloads.push(body);
      state.committed.set(body.choice.revisionId, body.choice.channels);
      return json(route, { version: 1, revisionId: body.choice.revisionId, status: "imported", scan: savedScan(body.choice.revisionId, body.choice.channels),
        receiptState: "acknowledged", reviewRequired: false, reviewed: false });
    });
    await page.route(/\/api\/community\/votes\/mixed(?:\/retry)?$/, (route) => {
      if (route.request().method() === "GET") {
        const pending = [...intents].find(([, intent]) => intent.state === "submitted");
        return json(route, { version: 1, revisionId: "mixed", scope: { serviceUrl: "https://community.example", contributorId: "reader" },
          current: { vote: currentVote, eligible: state.committed.has("mixed"), reason: state.committed.has("mixed") ? null : "import_required" },
          totals: { likes: currentVote === "like" ? 1 : 0, dislikes: currentVote === "dislike" ? 1 : 0 },
          pending: pending ? { operationId: pending[0], ...pending[1] } : null, error: null });
      }
      const body = route.request().postDataJSON(); sends.push(body);
      expect(route.request().headers()["x-csrf-token"]).toBe("test-csrf-token");
      if (!intents.has(body.operation_id)) {
        intents.set(body.operation_id, { vote: body.vote, state: "submitted" }); currentVote = body.vote;
      }
      const intent = intents.get(body.operation_id)!;
      if (loseReply) { loseReply = false; return json(route, { code: "network_unavailable", retryable: true }, 503); }
      intent.state = "acknowledged";
      return json(route, { version: 1, state: "completed", result: { version: 1, operationId: body.operation_id, revisionId: "mixed", vote: intent.vote } });
    });
    let dialog = await state.open();
    const matches = () => dialog.getByRole("table", { name: "Community filter matches" });
    const row = () => matches().getByRole("row").filter({ hasText: "Mixed review" });
    const thumbsUp = () => row().getByRole("button", { name: /^Thumbs up/ });
    await thumbsUp().click();
    await expect(row().getByText("An acknowledged import of this exact revision is required.")).toBeVisible();
    expect(sends).toHaveLength(0);
    await row().getByRole("button", { name: "Download Mixed review" }).click();
    await dialog.getByRole("dialog", { name: "Download filters" }).getByRole("button", { name: "Download filters" }).click();
    await expect(dialog.getByText(/Receipt acknowledged/)).toBeVisible();
    await thumbsUp().focus(); await page.keyboard.press("Space");
    await expect(row().getByRole("button", { name: "Retry saved vote" })).toBeVisible();
    const original = sends[0].operation_id;
    currentVote = "dislike";
    await page.keyboard.press("Escape");
    dialog = await state.open();
    await thumbsUp().click();
    await expect(row().getByRole("button", { name: "Retry saved vote" })).toBeVisible();
    await row().getByRole("button", { name: "Retry saved vote" }).click();
    const thumbsDown = row().getByRole("button", { name: /^Thumbs down/ });
    await expect(thumbsDown).toHaveAttribute("aria-pressed", "true");
    expect(sends).toHaveLength(2); expect(sends[1]).toEqual({ operation_id: original, confirmed: true });
    await thumbsDown.click();
    await expect(thumbsDown).toHaveAttribute("aria-pressed", "false");
    expect(sends[2].vote).toBe("neutral");
    expect(intents.size).toBe(2);
    await dialog.getByLabel("Channel").selectOption("video");
    await expect(row().getByRole("button", { name: "Downloaded Mixed review" })).toBeDisabled();
    expect(state.downloads).toHaveLength(1);
    expect(state.counts()).toEqual({ reviewWrites: 0, exportWrites: 0 });
    await expectNoAccessibilityViolations(page);
    await expectNoHorizontalPageOverflow(page);
    await row().scrollIntoViewIfNeeded();
    await page.screenshot({ path: `test-results/css54-feedback-${width}.png` });
  });

  test(`${width}px partial download, explicit retry/reuse, uncertain approval and saved-review attribution`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const state = await setup(page);
    let failSecond = true;
    await page.route("**/api/community/downloads", (route) => {
      const body = route.request().postDataJSON(); state.downloads.push(body);
      expect(route.request().headers()["x-csrf-token"]).toBe("test-csrf-token");
      expect(body.lookup.target).toMatchObject({ source: "jellyfin", item_id: "movie-1", media_source_id: "source-2" });
      expect(body.lookup.audio_selection).toEqual(selection);
      expect(body.lookup.target_key).toBe("a".repeat(64));
      if (body.choice.revisionId === "second" && failSecond) { failSecond = false; return json(route, { code: "service_unavailable", retryable: true }, 409); }
      state.committed.set(body.choice.revisionId, body.choice.channels);
      return json(route, { version: 1, revisionId: body.choice.revisionId, status: "imported", scan: savedScan(body.choice.revisionId, body.choice.channels),
        receiptState: body.choice.revisionId === "second" ? "unavailable" : "pending", reviewRequired: body.choice.channels.includes("audio"), reviewed: false });
    });
    let dialog = await state.open();
    await dialog.getByRole("button", { name: "Download Mixed review" }).click();
    await dialog.getByRole("dialog", { name: "Download filters" }).getByRole("button", { name: "Download filters" }).click();
    await expect(dialog.getByText(/Receipt pending/)).toBeVisible();
    await dialog.getByRole("button", { name: "Download Second scan" }).click();
    await expect(dialog.getByText("The community service is unavailable.")).toBeVisible();
    await expect(dialog.getByText(/Receipt pending/)).toBeVisible();
    expect(state.downloads).toHaveLength(2);
    expect(state.counts()).toEqual({ reviewWrites: 0, exportWrites: 0 });
    await dialog.getByRole("button", { name: "Retry Second scan" }).click();
    await expect(dialog.getByText(/Receipt unavailable/)).toBeVisible();
    expect(state.downloads).toHaveLength(3);
    await expectNoAccessibilityViolations(page);
    await expectNoHorizontalPageOverflow(page);
    await page.screenshot({ path: `test-results/css50-downloads-${width}.png` });
    await page.keyboard.press("Escape");
    dialog = await state.open();
    await dialog.getByLabel("Channel").selectOption("video");
    await expect(dialog.getByRole("button", { name: "Downloaded Mixed review" })).toBeDisabled();
    await expect(dialog.getByRole("button", { name: "Downloaded Second scan" })).toBeDisabled();
    expect(state.downloads).toHaveLength(3);
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "New Review", exact: true }).click();
    await page.getByLabel("New review name").fill("Community assembly");
    await page.getByRole("button", { name: "Start new review" }).click();
    await expect(page.getByText("Source author: signed-author").first()).toBeVisible();
    await page.getByRole("checkbox", { name: /Community mixed/ }).check();
    await expect(page.getByRole("combobox", { name: /Review role for Community mixed/ })).toHaveCount(0);
    await page.getByRole("button", { name: "Save Review", exact: true }).click();
    await expect.poll(() => state.counts().reviewWrites).toBe(1);
    expect(state.savedMemberships[0]).toContainEqual({ scan_id: "saved-mixed", role: "video" });
    await expect(page.getByText("Source author: signed-author").first()).toBeVisible();
    dialog = await state.open();
    await dialog.getByLabel("Channel").selectOption("audio");
    await dialog.getByRole("button", { name: "Download Mixed review" }).click();
    const channelDialog = dialog.getByRole("dialog", { name: "Download filters" });
    const download = channelDialog.getByRole("button", { name: "Download filters" });
    await expect(download).toBeDisabled();
    await channelDialog.getByRole("checkbox", { name: /I reviewed the timing/ }).check();
    await download.click();
    await expect.poll(() => state.downloads.length).toBe(4);
    expect(state.downloads[3].confirmed).toBe(true);
    expect(state.counts()).toEqual({ reviewWrites: 1, exportWrites: 0 });
  });

  test(`${width}px cancellation and reopen reconcile a late committed import`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const state = await setup(page);
    let finish!: () => void;
    const gate = new Promise<void>((resolve) => { finish = resolve; });
    await page.route("**/api/community/downloads", async (route) => {
      const body = route.request().postDataJSON(); state.downloads.push(body);
      state.committed.set(body.choice.revisionId, body.choice.channels);
      await gate;
      try { await json(route, { version: 1, revisionId: body.choice.revisionId, status: "imported", scan: savedScan(body.choice.revisionId, body.choice.channels), receiptState: "pending", reviewRequired: false, reviewed: false }); } catch {}
    });
    let dialog = await state.open();
    await dialog.getByLabel("Channel").selectOption("video");
    await dialog.getByRole("button", { name: "Download Mixed review" }).click();
    await expect(dialog.getByRole("button", { name: "Cancel downloads" })).toBeVisible();
    await expect.poll(() => state.downloads.length).toBe(1);
    await dialog.getByRole("button", { name: "Cancel downloads" }).click();
    await expect(dialog.getByText("Outcome unknown; check download status.")).toBeVisible();
    await page.keyboard.press("Escape"); finish();
    dialog = await state.open();
    await expect(dialog.getByText("Outcome unknown; check download status.")).toHaveCount(0);
    await expect(dialog.getByText(/Receipt pending/)).toBeVisible();
    expect(state.downloads).toHaveLength(1);
    expect(state.counts()).toEqual({ reviewWrites: 0, exportWrites: 0 });
    await expectNoAccessibilityViolations(page);
    await expectNoHorizontalPageOverflow(page);
    await page.screenshot({ path: `test-results/css50-reconciled-${width}.png` });
  });
}

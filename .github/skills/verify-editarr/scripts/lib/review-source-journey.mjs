import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { capture } from "./capture.mjs";
import { probedDuration } from "./synthetic-jellyfin.mjs";

export function prepareReviewMedia(run, media) {
  const chapters = path.join(run.scratch, "review-chapters.ffmeta");
  fs.writeFileSync(chapters, ";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=3000\nEND=4000\ntitle=End Credits\n");
  const file = path.join(path.dirname(media.file), "review-credits.mkv");
  execFileSync(media.ffmpeg, ["-nostdin", "-v", "error", "-y", "-i", media.file, "-i", chapters,
    "-map", "0", "-map_metadata", "0", "-map_chapters", "1", "-c", "copy", file], { timeout: 60000 });
  return { ...media, fixture: "synthetic-av-v1-with-credits-chapter", file,
    durationSeconds: probedDuration(run, file), sha256: createHash("sha256").update(fs.readFileSync(file)).digest("hex") };
}

export async function verifySourceReviews({ run, member, ownerContext, memberId, width, send, expect, axe, media, library, item }) {
  const { page, context } = member;
  const sources = [
    { source: "jellyfin", route: `/#/library/${library}/item/${item}`, title: "Synthetic catalog film" },
  ];
  const results = [];
  async function response(method, suffix, action, status = 200) {
    const [result] = await Promise.all([
      page.waitForResponse(value => value.request().method() === method && new URL(value.url()).pathname.endsWith(suffix)),
      action(),
    ]);
    if (result.status() !== status) assert.fail(`${method} ${suffix}: expected HTTP ${status}, received ${result.status()}: ${await result.text()}`);
    return status === 204 ? {} : result.json();
  }
  async function read(id) {
    const result = await send(context, "GET", `/api/reviews/${id}`);
    assert.equal(result.status(), 200);
    return result.json();
  }
  for (const source of sources) {
    await page.goto(`${run.manifest.origin}${source.route}`);
    await expect(page.getByRole("heading", { name: source.title, exact: true })).toBeVisible();
    const panel = page.locator(".saved-review-panel");
    const name = `${source.source} member ${width}`;
    await panel.getByRole("button", { name: "Import", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Import", exact: true });
    await expect(dialog.getByRole("button", { name: "EDL", exact: true })).toBeFocused();
    await expect(dialog.getByRole("button", { name: "Scanner path", exact: true })).toHaveCount(0);
    await dialog.getByLabel("Import review name", { exact: true }).fill(name);
    await dialog.getByLabel("EDL file", { exact: true }).setInputFiles({ name: "source.edl", mimeType: "text/plain", buffer: Buffer.from("1 2 1\n") });
    const imported = await response("POST", "/api/edl-imports/upload", () => dialog.getByRole("button", { name: "Import EDL", exact: true }).click(), 201);
    let review = await read(imported.review.id);
    assert.equal(review.owner_id, memberId);
    assert.equal(review.target.source, source.source);
    run.write(`mu33-${source.source}-target-${width}.json`, { target: review.target, owner: review.owner_id });
    await expect(panel.getByRole("heading", { name, exact: true })).toBeVisible();
    await panel.getByRole("button", { name: "Adjust Cue Times", exact: true }).click();
    const timing = page.getByRole("dialog", { name: "Adjust Cue Times", exact: true });
    await expect(timing.getByLabel("Proposed START", { exact: true })).toBeFocused();
    await timing.getByLabel("Proposed START", { exact: true }).fill("00:00:00.100");
    await response("POST", `/api/reviews/${review.id}/retime`, () => timing.getByRole("button", { name: "Apply to draft", exact: true }).click());
    await response("PUT", `/api/reviews/${review.id}`, () => panel.getByRole("button", { name: "Save Changes", exact: true }).click());
    review = await read(review.id);
    assert.equal(review.synchronization.start_seconds, 0.1);
    const started = await send(context, "POST", "/api/credits-detections", { target: review.target, qwen_model: null });
    assert.equal(started.status(), 202, await started.text());
    const scanId = (await started.json()).scan.id;
    let detection;
    await expect.poll(async () => {
      const current = await send(context, "GET", `/api/credits-detections/${scanId}`);
      assert.equal(current.status(), 200);
      detection = await current.json();
      return detection.status;
    }, { timeout: 60000 }).toBe("complete");
    assert.equal(detection.credits_detection.result.outcome, "candidate");
    await page.reload();
    await panel.getByRole("button", { name: `Open review ${name}`, exact: true }).click();
    await expect(panel.getByRole("heading", { name, exact: true })).toBeVisible();
    await expect(panel.getByRole("button", { name: "Review Credits Result", exact: true })).toBeEnabled();
    const clip = page.waitForResponse(value => /\/api\/credits-detections\/[^/]+\/clip/.test(value.url()));
    await panel.getByRole("button", { name: "Review Credits Result", exact: true }).click();
    const credits = page.getByRole("dialog", { name: "Review Credits Result", exact: true });
    await expect(credits).toBeVisible();
    assert.equal((await clip).status(), 200);
    await credits.getByLabel("Accepted credits boundary", { exact: true }).fill("00:00:03.000");
    await capture(page, run, `mu33-${source.source}-credits-${width}`);
    await axe(page, `mu33-${source.source}-credits-${width}`);
    await response("POST", `/api/credits-detections/${scanId}/accept`, () => credits.getByRole("button", { name: "Accept boundary", exact: true }).click());
    await response("PUT", `/api/reviews/${review.id}`, () => panel.getByRole("button", { name: "Save Changes", exact: true }).click());
    review = await read(review.id);
    assert.equal(review.accepted_credits.scan_id, scanId);
    assert.equal(review.accepted_credits.accepted_boundary_seconds, 3);
    assert.equal(review.accepted_credits.detected_boundary_seconds,
      detection.credits_detection.result.candidates.find(value => value.id === detection.credits_detection.result.best_candidate_id).boundary_seconds);
    await panel.getByRole("button", { name: "Duplicate", exact: true }).click();
    const duplicate = page.getByRole("dialog", { name: "Duplicate Review", exact: true });
    await expect(duplicate.getByLabel("Duplicate review name", { exact: true })).toBeFocused();
    const copyName = `${name} copy`;
    await duplicate.getByLabel("Duplicate review name", { exact: true }).fill(copyName);
    const copy = await response("POST", `/api/reviews/${review.id}/duplicate`, () => duplicate.getByRole("button", { name: "Duplicate", exact: true }).click(), 201);
    assert.equal(copy.owner_id, memberId);
    assert.equal(copy.accepted_credits.scan_id, scanId);
    await expect(panel.getByRole("heading", { name: copyName, exact: true })).toBeVisible();
    await panel.getByRole("button", { name: "Rename review", exact: true }).click();
    await panel.getByLabel("Review name", { exact: true }).fill(`${copyName} renamed`);
    await response("PATCH", `/api/reviews/${copy.id}`, () => panel.getByRole("button", { name: "Save", exact: true }).click());
    assert.equal((await read(copy.id)).name, `${copyName} renamed`);
    await expect(panel.getByRole("button", { name: "Save Changes", exact: true })).toBeDisabled();
    await capture(page, run, `mu33-${source.source}-saved-${width}`);
    await axe(page, `mu33-${source.source}-saved-${width}`);
    const ownerCopy = await send(ownerContext, "POST", `/api/reviews/${review.id}/duplicate`, {
      revision: review.revision, policy_owner_id: review.policy_owner_id, policy_revision: review.policy_revision, name: `${source.source} owner private ${width}`,
    });
    assert.equal(ownerCopy.status(), 201, await ownerCopy.text());
    const privateReview = await ownerCopy.json();
    assert.equal((await send(context, "GET", `/api/reviews/${privateReview.id}`)).status(), 404);
    await panel.getByRole("button", { name: "Delete review", exact: true }).click();
    const deletion = page.getByRole("dialog", { name: "Delete review", exact: true });
    await expect(deletion.getByText("No published files you can see come from this review.", { exact: true })).toBeVisible();
    await response("POST", `/api/reviews/${copy.id}/deletion`, () => deletion.getByRole("button", { name: "Delete review", exact: true }).click());
    assert.equal((await send(context, "GET", `/api/reviews/${copy.id}`)).status(), 404);
    results.push({ source: source.source, width, review: review.id, owner: memberId, credits: scanId,
      imported: true, retimed: true, acceptedCredits: true, copied: true, renamed: true, deleted: true, privateGuessedId: 404 });
    run.write(`mu33-source-reviews-${width}.json`, results);
  }
  assert.equal(createHash("sha256").update(fs.readFileSync(media.file)).digest("hex"), media.sha256);
}
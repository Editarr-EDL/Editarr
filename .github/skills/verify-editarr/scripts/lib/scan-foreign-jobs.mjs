import assert from "node:assert/strict";
import fs from "node:fs";
import { query } from "./database.mjs";

/**
 * The owner queues two scans of the Jellyfin item from its catalog page: one that the
 * synthetic model interrupts, leaving an active orphaned job with an error, and one
 * queued behind it. The member's queue and history API, the floating queue panel and its
 * badge, and the member's own queued job must show none of their IDs, names, progress,
 * errors, or count, and every caller's queue positions count only the jobs it sees.
 * The owner then cancels one job and resumes the other to completion. The completed scan
 * is then shared evidence: the member reads it, without its server path.
 */
export async function foreignJobs({ run, owner, member, ownerContext, media, interrupt, submit, send, expect, capture, axe }) {
  const origin = run.manifest.origin;
  const positions = list => [list.map(value => value.sort_order), list.map((_, index) => index)];
  const queue = async context => { const response = await send(context, "GET", "/api/scans/queue"); assert.equal(response.status(), 200); return response.json(); };
  const history = async context => { const response = await send(context, "GET", "/api/scans/history"); assert.equal(response.status(), 200); return response.json(); };
  const page = owner.page;

  const queueOwn = async name => submit(owner, "video", name, "foreign-owner-1440");
  fs.writeFileSync(interrupt, "synthetic model interruption");
  const held = await queueOwn("Owner held scan");
  await expect.poll(async () => (await queue(ownerContext)).find(value => value.id === held.id)?.status, { timeout: 60_000 }).toBe("orphaned");
  fs.unlinkSync(interrupt);
  const waiting = await queueOwn("Owner queued scan");
  const ownerView = await queue(ownerContext);
  const [heldJob, waitingJob] = [held, waiting].map(job => ownerView.find(value => value.id === job.id));
  assert.equal(heldJob.status, "orphaned"); assert.ok(heldJob.error_message, "the owner sees the held job's error");
  assert.equal(waitingJob.status, "queued", "the second owner job waits behind the orphaned one");
  assert.deepEqual(...positions(ownerView), "the owner's queue positions are 0..n-1 in queue order");
  await capture(page, run, "scan-owner-queued-1440");
  const secrets = [held.id, waiting.id, "Owner held scan", "Owner queued scan", heldJob.error_message, "unrelated-model-cache", media.file];
  const absent = (text, label) => { for (const value of secrets) assert.ok(!text.includes(value), `${label} omits ${value}`); };

  const { page: memberPage, context } = member;
  const before = await queue(context);
  assert.deepEqual(before, [], "the member's queue lists no other user's job");
  absent(JSON.stringify(before), "member queue API");
  absent(JSON.stringify(await history(context)), "member history API");
  for (const job of ownerView) {
    assert.equal((await send(context, "DELETE", `/api/scans/queue/${job.queue_id}`)).status(), 404, "a foreign job cannot be cancelled by ID");
    assert.equal((await send(context, "POST", `/api/scans/queue/${job.queue_id}/resume`)).status(), 404, "a foreign job cannot be resumed by ID");
  }
  const panel = async expected => {
    await memberPage.getByRole("button", { name: "Open scans queue and history", exact: true }).click();
    const region = memberPage.getByRole("region", { name: "Scans queue and history", exact: true });
    await expect(region).toBeVisible();
    await expect(memberPage.locator(".scans-floating-badge")).toHaveText(String(expected));
    const rows = region.getByRole("region", { name: "Scan queue", exact: true }).locator("tbody tr");
    await expect(rows).toHaveCount(expected);
    absent(await memberPage.locator("body").ariaSnapshot(), `member queue panel with ${expected} own jobs`);
    absent(await memberPage.locator("body").innerText(), `member page text with ${expected} own jobs`);
    return region;
  };
  await panel(0);
  await capture(memberPage, run, "scan-foreign-queue-empty-1440");
  await axe(memberPage, "scan-foreign-queue-empty-1440");
  await memberPage.getByRole("button", { name: "Collapse scans panel", exact: true }).click();

  const own = await submit(member, "video", "Member behind foreign", "foreign-1440");
  const after = await queue(context);
  assert.deepEqual(after.map(value => value.id), [own.id], "the member's queue lists only its own job");
  absent(JSON.stringify(after), "member queue API with its own job");
  assert.equal(own.sort_order, 0, "the member's create response counts none of the hidden jobs ahead of it");
  assert.equal(after[0].sort_order, 0, "the member's own entry counts none of the hidden jobs ahead of it");
  const ownerWithMember = await queue(ownerContext);
  assert.deepEqual(ownerWithMember.map(value => value.id), [held.id, waiting.id, own.id], "the owner sees the member's job behind both of its own");
  assert.deepEqual(...positions(ownerWithMember), "the owner's positions still count every job");
  const reorder = await reorderQueue({ run, page, context, ownerContext, queue, send, expect, capture, jobs: ownerWithMember });
  const region = await panel(1);
  assert.equal(await region.getByRole("button", { name: /^Move / }).count(), 0, "the member's panel offers no reorder control");
  await capture(memberPage, run, "scan-foreign-queue-own-1440");
  await region.locator("tbody tr").first().getByRole("button", { name: /^(Cancel|Remove)$/ }).click();
  await memberPage.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect.poll(async () => (await queue(context)).length).toBe(0);
  await memberPage.getByRole("button", { name: "Collapse scans panel", exact: true }).click();

  assert.equal(await memberPage.getByLabel("Video file path", { exact: true }).count(), 0, "the member's catalog page has no path field");

  assert.equal((await send(ownerContext, "DELETE", `/api/scans/queue/${waitingJob.queue_id}`)).status(), 200);
  assert.equal((await send(ownerContext, "POST", `/api/scans/queue/${heldJob.queue_id}/resume`)).status(), 200);
  await expect.poll(async () => (await history(ownerContext)).find(value => value.id === held.id)?.status, { timeout: 60_000 }).toBe("complete");
  const memberHistory = await history(context);
  absent(JSON.stringify(memberHistory.filter(value => value.id !== held.id)), "member history after completion");
  const completed = memberHistory.find(value => value.id === held.id);
  assert.ok(completed, "the owner's completed scan is shared evidence the member may read: the member's default scope reaches the Jellyfin item, and the permission matrix lets a user read completed findings for authorized media");
  assert.ok(!JSON.stringify(completed).includes(media.file), "the completed scan the member reads carries no server path");
  const result = {
    owner: { held: { id: held.id, status: heldJob.status, progress: heldJob.progress_percent, hasError: Boolean(heldJob.error_message) }, waiting: { id: waiting.id, status: waitingJob.status } },
    positions: { ownerBefore: ownerView.map(value => value.sort_order), member: { created: own.sort_order, listed: after[0].sort_order, foreignJobsAhead: ownerView.length }, ownerWithMember: ownerWithMember.map(value => value.sort_order) },
    reorder,
    member: { queueBefore: before.length, queueWithOwn: after.length, completedOwnerScanVisible: true },
  };
  run.write("scan-foreign-jobs.json", result);
  await memberPage.goto(`${origin}/#/`);
  return result;
}

/**
 * The member's reorder request is refused and moves nothing; the owner then moves the
 * member's job ahead of its own waiting job from the queue panel, and the stored
 * order and the owner's queue API both follow.
 */
async function reorderQueue({ run, page, context, ownerContext, queue, send, expect, capture, jobs }) {
  const persisted = () => query(run, "SELECT id FROM scan_queue ORDER BY sort_order, id").map(row => row.id);
  const before = jobs.map(job => job.queue_id);
  assert.deepEqual(persisted(), before, "the stored order matches the owner's queue");
  const refused = await send(context, "POST", "/api/scans/queue/reorder", { queue_ids: [before[2]] });
  const refusal = [refused.status(), (await refused.json()).code];
  assert.deepEqual(refusal, [403, "capability_denied"], "the member may not reorder the queue");
  assert.deepEqual(persisted(), before, "a refused reorder moves nothing");

  await page.getByRole("button", { name: "Open scans queue and history", exact: true }).click();
  const rows = page.getByRole("region", { name: "Scan queue", exact: true }).locator("tbody tr");
  await expect(rows).toHaveCount(3);
  const [response] = await Promise.all([
    page.waitForResponse(value => new URL(value.url()).pathname === "/api/scans/queue/reorder" && value.request().method() === "POST"),
    rows.nth(2).getByRole("button", { name: /^Move .* up$/ }).click(),
  ]);
  assert.equal(response.status(), 200, await response.text());
  const after = [before[0], before[2], before[1]];
  assert.deepEqual(persisted(), after, "the owner's reorder is stored");
  assert.deepEqual((await queue(ownerContext)).map(job => job.queue_id), after, "the owner's queue API reads the stored order back");
  await capture(page, run, "scan-queue-owner-reordered-1440");
  await page.getByRole("button", { name: "Collapse scans panel", exact: true }).click();
  return { before, refused: refusal, after };
}

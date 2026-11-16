import assert from "node:assert/strict";
import { query } from "./database.mjs";

const MCF = "WEBVTT MovieContentFilter 1.1.0\n\nNOTE\nTITLE Upload proof\nTYPE movie\n\nNOTE\nSTART 00:00:00.000\nEND 00:00:04.000\n\n00:00:01.000 --> 00:00:02.000\nviolence=low=video\n";
const EDL = "1.000 2.000 1\n";

/**
 * Prove that an EDL or MCF import is a browser upload only. The removed server-path
 * routes answer 404, an upload naming another source is refused, a malformed Jellyfin
 * target is a validation error, and the owner's uploads save owner rows. Saved rows are
 * checked rather than responses, and the owner's imports are deleted afterwards so later
 * journeys see the same data.
 */
export async function verifyUploadImportsOnly(run, { api, owner, member, target, media, sameMediaAs }) {
  const options = {
    edl: (name, goal = target) => ({ target: goal, name }),
    mcf: (name, goal = target) => ({ target: goal, name, synchronization: { start_seconds: 0, end_seconds: media.durationSeconds, end_mode: "file_end" }, mcf_material_end_seconds: 4 }),
  };
  const file = { edl: { name: "upload.edl", mimeType: "text/plain", buffer: Buffer.from(EDL) }, mcf: { name: "upload.mcf", mimeType: "text/vtt", buffer: Buffer.from(MCF) } };
  const upload = (who, format, body, status) => api(who, "POST", `/api/${format}-imports/upload`, null, status, { options: JSON.stringify(body), file: file[format] });
  const counts = () => query(run, "SELECT (SELECT count(*) FROM saved_reviews) AS reviews, (SELECT count(*) FROM scans) AS scans")[0];
  const [{ id: ownerId }] = query(run, "SELECT id FROM users WHERE role = 'owner'");
  const [{ media_item_id: mediaItem }] = query(run, "SELECT media_item_id FROM saved_reviews WHERE id = ?", sameMediaAs);
  const result = { pathRoutes: {}, refused: {}, malformed: {}, imported: {} };
  for (const format of ["edl", "mcf"]) {
    const before = counts();
    await api(owner, "POST", `/api/${format}-imports/path`, { ...options[format]("Owner path"), [`${format}_path`]: `/media/server.${format}` }, 404);
    assert.deepEqual(counts(), before, `the removed ${format} path route saves nothing`);
    result.pathRoutes[format] = { status: 404, unchanged: true };

    const refused = await upload(member, format, options[format]("Other source", { source: "manual", video_path: "/media/other.mkv" }), 400);
    assert.equal(refused.detail.code, "jellyfin_item_required");
    assert.deepEqual(counts(), before, `a ${format} upload naming another source saves nothing`);
    result.refused[format] = { status: 400, code: refused.detail.code, unchanged: true };

    const malformed = await upload(member, format, options[format]("Malformed", { source: "jellyfin", item_id: "" }), 422);
    assert.equal(malformed.detail.code, format === "edl" ? "invalid_edl_import" : "invalid_mcf_document");
    assert.deepEqual(counts(), before, `a malformed ${format} Jellyfin target saves nothing`);
    result.malformed[format] = { status: 422, code: malformed.detail.code, unchanged: true };

    const imported = await upload(owner, format, options[format](`Owner upload ${format}`), 201);
    const [saved] = query(run, "SELECT name, owner_id, media_item_id FROM saved_reviews WHERE id = ?", imported.review.id);
    assert.deepEqual(saved, { name: `Owner upload ${format}`, owner_id: ownerId, media_item_id: mediaItem });
    assert.deepEqual(counts(), { reviews: before.reviews + 1, scans: before.scans + 1 });
    result.imported[format] = { status: 201, review: imported.review.id, scan: imported.scan.id, owner: ownerId };
    await api(owner, "DELETE", `/api/reviews/${imported.review.id}?revision=${imported.review.revision}`, null, 204);
    await api(owner, "DELETE", `/api/scans/${imported.scan.id}`);
    assert.deepEqual(counts(), before, `the owner's ${format} import is removed again`);
  }
  run.write("review-upload-imports.json", { passed: true, ...result, scope: "Real authenticated source app: the removed EDL and MCF path routes answered 404, a member upload naming another source got 400 jellyfin_item_required and a malformed Jellyfin target 422, both saving nothing, and the owner's EDL and MCF uploads saved owner rows on the Jellyfin item." });
}

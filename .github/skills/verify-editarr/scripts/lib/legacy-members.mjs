import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { synthesizeMedia } from "./fixtures.mjs";
import { childEnvironment, pythonPath } from "./prerequisites.mjs";
import { startRunJellyfin } from "./run-jellyfin.mjs";
import { LIBRARY, probedDuration, startSyntheticJellyfin } from "./synthetic-jellyfin.mjs";

/** The synthetic Jellyfin movie that holds the legacy member's reviews. */
export const LEGACY_FILM = { id: "9".repeat(32), version: "8".repeat(32), title: "Legacy member film" };
const LEGACY_DURATION_SECONDS = 120;

/**
 * Before launch, seed the run's database as a JF-05 installation with local password
 * accounts (`legacy_members_fixture.py`): the owner linked to the run Jellyfin's
 * administrator, `legacy-member` owning reviews on LEGACY_FILM and a personal EDL and
 * MCF under its local export ID, and `legacy-linker`.
 * Startup applies 0030, which disables both members with cause `unlinked` and moves
 * each username to its display name. The run's
 * synthetic Jellyfin starts here, serving FILM and LEGACY_FILM, so `verifyReviews`
 * reuses it and its media.
 */
export async function seedLegacyMembers(run) {
  const jellyfin = await startRunJellyfin(run);
  await jellyfin.call("POST", "/Auth/Keys?app=Editarr");
  const [key] = await jellyfin.editarrKeys();
  const media = synthesizeMedia(run.root, run.scratch, run.data);
  run.reviewMedia = media;
  run.provider = await startSyntheticJellyfin(run, { ...media, durationSeconds: probedDuration(run, media.file) }, { port: await run.ports.next() });
  const file = path.join(fs.realpathSync(path.dirname(media.file)), "legacy", "Movie.mkv");
  run.provider.add(LEGACY_FILM, file, LEGACY_DURATION_SECONDS);
  const output = execFileSync(pythonPath(run.root), ["-B", "-P", path.join(run.root, ".github/skills/verify-editarr/scripts/legacy_members_fixture.py"), run.data,
    JSON.stringify({ server: run.provider.url, library: LIBRARY, item: LEGACY_FILM.id, version: LEGACY_FILM.version, title: LEGACY_FILM.title, file })], {
    cwd: run.root, env: childEnvironment(run.root, run.data), encoding: "utf8", timeout: 120000,
    input: JSON.stringify({ base: jellyfin.base, key, serverId: jellyfin.serverId, adminId: jellyfin.admin.id }),
  });
  const fixture = JSON.parse(output.trim().split("\n").at(-1));
  assert.equal(fixture.revision, "0029_jellyfin_user_blocks");
  assert.deepEqual(fixture.linkedUsers, [1], "only the owner is linked before startup");
  assert.deepEqual(fixture.foreignKeyViolations, []);
  assert.deepEqual(fixture.exports.map(([format, scope, user]) => [format, scope, user]), [["edl", "personal", fixture.members["legacy-member"]], ["mcf", "personal", fixture.members["legacy-member"]]]);
  run.secrets.add("d".repeat(64));
  run.write("legacy-members-fixture.json", fixture);
  run.environment = { ...run.environment, EDITARR_VERIFY_SEEDED_DATABASE: path.join(fs.realpathSync(run.data), "scanner.db") };
  run.legacyMembers = {
    member: { id: fixture.members["legacy-member"], name: "legacy-member", reviews: fixture.reviews.map(([id, name]) => ({ id, name })) },
    linker: { id: fixture.members["legacy-linker"], name: "legacy-linker" },
    accounts: fixture.members, film: LEGACY_FILM, file,
  };
}

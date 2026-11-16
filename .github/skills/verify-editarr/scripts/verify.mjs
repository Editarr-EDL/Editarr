#!/usr/bin/env node
import { once } from "node:events";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { authenticate, expectSetupScreen } from "./lib/auth-journey.mjs";
import { verifyAuditHistory } from "./lib/audit-journey.mjs";
import { synthesizeMedia } from "./lib/fixtures.mjs";
import { cleanup, createRun, inspect, launch, readInstance, ready, stopOnSignals } from "./lib/instance.mjs";
import { rendezvous, runPair } from "./lib/pair.mjs";
import { communityPrerequisites, exitBlocked, missingPrerequisites, MODE_REQUIREMENTS } from "./lib/prerequisites.mjs";
import { verifyBrowserSessions } from "./lib/session-journey.mjs";
import { seedLegacyDatabase, verifyUpgrade } from "./lib/upgrade-journey.mjs";
import { verifyUsers } from "./lib/users-journey.mjs";
import { verifyReviews } from "./lib/review-journey.mjs";
import { parsePortRange } from "./lib/ports.mjs";
import { verifySettings } from "./lib/settings-journey.mjs";
import { prepareCommunity, verifyCommunity } from "./lib/community-journey.mjs";

const script = fileURLToPath(import.meta.url);
const root = path.resolve(path.dirname(script), "../../../..");
const USAGE = "Usage: verify.mjs auth [--fixed-clock] [--inject-failure] [--reviews [--account-link] [--account-deletion]] [--users | --audit | --audit-inject-failure | --settings | --identity | --community | --plugin-interop | --quick-connect | --jellyfin-accounts] [--controller] [--ports=A-B] | probe [--ports=A-B] | serve [--controller] [--ports=A-B] | upgrade [--ports=A-B] | repairs [--ports=A-B] | restore [--ports=A-B] | pair [--ports=A-B] | doctor EVIDENCE | media EVIDENCE";
const [mode = "auth", ...rest] = process.argv.slice(2);
const flags = new Set(rest.filter(value => value.startsWith("--") && !value.startsWith("--barrier=") && !value.startsWith("--ports=")));
const portOptions = rest.filter(value => value.startsWith("--ports="));
const portRange = portOptions[0]?.slice("--ports=".length);
let validPorts = portOptions.length <= 1 && (!portRange || ["auth", "serve", "probe", "upgrade", "repairs", "restore", "pair"].includes(mode));
try { parsePortRange(portRange); } catch { validPorts = false; }
const barrier = rest.find(value => value.startsWith("--barrier="))?.slice("--barrier=".length);
const target = rest.find(value => !value.startsWith("--"));
const allowedFlags = { auth: ["--fixed-clock", "--inject-failure", "--reviews", "--account-link", "--account-deletion", "--users", "--audit", "--audit-inject-failure", "--settings", "--identity", "--controller", "--community", "--plugin-interop", "--quick-connect", "--jellyfin-accounts"], serve: ["--controller"] }[mode] ?? [];
const needsTarget = ["doctor", "media"].includes(mode);
const journeys = [["--community"], ["--users"], ["--audit", "--audit-inject-failure"], ["--identity"], ["--settings"], ["--plugin-interop"], ["--quick-connect"], ["--jellyfin-accounts"]].filter(group => group.some(flag => flags.has(flag)));
const runsOneJourney = journeys.length <= 1 && !((flags.has("--identity") || flags.has("--plugin-interop")) && flags.has("--reviews"));
if (!runsOneJourney || !validPorts || !(mode in MODE_REQUIREMENTS) || [...flags].some(flag => !allowedFlags.includes(flag)) || needsTarget !== Boolean(target)) {
  console.error(USAGE);
  process.exit(64);
}
const missing = missingPrerequisites(mode, root);
if ((flags.has("--account-link") || flags.has("--account-deletion")) && !flags.has("--reviews")) { console.error(USAGE); process.exit(64); }
if (flags.has("--community")) missing.push(...missingPrerequisites("media", root), ...communityPrerequisites(root));
if (flags.has("--reviews") || flags.has("--plugin-interop")) missing.push(...missingPrerequisites("media", root));
if (flags.has("--identity") && !missing.length) missing.push(...(await import("./lib/identity-journey.mjs")).identityPrerequisites(root));
if (missing.length) exitBlocked(root, mode, missing);

if (mode === "doctor" || mode === "media") {
  try {
    const instance = readInstance(target);
    const report = await inspect(instance, root);
    if (mode === "media") {
      const media = synthesizeMedia(root, path.dirname(instance.data), instance.data);
      fs.writeFileSync(path.join(target, "media.json"), `${JSON.stringify(media, null, 2)}\n`, { mode: 0o600 });
      console.log(JSON.stringify(media, null, 2));
    } else console.log(JSON.stringify(report, null, 2));
    process.exit(0);
  } catch (error) {
    console.error(`FAIL ${mode}: ${error.message}`);
    process.exit(1);
  }
}
if (mode === "pair") process.exit(await runPair(root, script, portRange));
if (mode === "repairs") {
  const { runReviewRepairs } = await import("./lib/repair-journey.mjs");
  process.exit(await runReviewRepairs(root, portRange));
}
if (mode === "restore") {
  const { runRestore } = await import("./lib/restore-journey.mjs");
  process.exit(await runRestore(root, portRange));
}

const run = createRun(root, { portRange });
run.accountLink = flags.has("--account-link");
run.accountDeletion = flags.has("--account-deletion");
stopOnSignals(run);
try {
  if (mode === "upgrade") seedLegacyDatabase(run);
  if (flags.has("--jellyfin-accounts")) (await import("./lib/jellyfin-accounts-journey.mjs")).seedImportOnlyDatabase(run);
  if (flags.has("--settings")) run.environment = { EDITARR_COMMUNITY_SERVICE_URL: "https://community.test", EDITARR_COMMUNITY_SERVICE_AUDIENCE: "community.test" };
  if (flags.has("--community")) prepareCommunity(run);
  if (run.accountLink || run.accountDeletion) await (await import("./lib/legacy-members.mjs")).seedLegacyMembers(run);
  await launch(run, { controller: flags.has("--controller") || flags.has("--community"), community: flags.has("--community") });
  console.log(`Evidence: ${run.evidence}`);
  await ready(run, mode === "upgrade" || run.legacyMembers ? "unauthenticated" : undefined);
  const report = await inspect(run.manifest, root);
  run.write("doctor.json", report);
  console.log(`Doctor passed: ${report.origin}, ${report.authentication} gate, owned loopback listeners, ${report.logFiles.length} open log files inside the run.`);
  if (barrier) await rendezvous(barrier, run);
  if (mode === "auth") {
    const setUp = async () => {
      const owner = await authenticate(run, { fixedClock: flags.has("--fixed-clock"), injectFailure: flags.has("--inject-failure"), libraries: flags.has("--identity") });
      console.log(run.legacyMembers ? "PASS: the seeded owner signed in with Jellyfin, one minted Editarr key, protected access, sign-out, Jellyfin sign-in, and desktop/mobile evidence." : "PASS: Jellyfin administrator setup, one minted Editarr key, protected access, sign-out, Jellyfin sign-in, and desktop/mobile evidence.");
      return owner;
    };
    // The plugin journey sets Editarr up against the plugin's own Jellyfin once it is running.
    const owner = flags.has("--plugin-interop") ? null : await setUp();
    if (flags.has("--plugin-interop")) {
      const { verifyPluginInterop } = await import("./lib/plugin-interop-journey.mjs");
      await verifyPluginInterop(run, setUp);
    } else if (flags.has("--community")) {
      await verifyCommunity(run, owner);
      console.log("PASS: own and named community connection, original and acting contributor, stale preview refresh, desktop/mobile axe and overflow.");
    } else if (flags.has("--users")) {
      const { verifyUsersUI } = await import("./lib/users-ui-journey.mjs");
      await verifyUsersUI(run, owner);
    } else if (flags.has("--audit") || flags.has("--audit-inject-failure")) {
      await verifyAuditHistory(run, owner, { injectFailure: flags.has("--audit-inject-failure") });
      console.log("PASS: permission-scoped audit UI/API, retention refresh, capability change, and account isolation.");
    } else if (flags.has("--identity")) {
      const { verifyIdentity } = await import("./lib/identity-journey.mjs");
      await verifyIdentity(run, owner);
      console.log("PASS: owner and five Jellyfin users with their own library policies kept counts, errors, artwork, preview bytes, storage, deep links, keys, and in-session changes apart at 1440 and 320 with keyboard, loading, and error states.");
    } else if (flags.has("--quick-connect")) {
      const { verifyQuickConnect } = await import("./lib/quick-connect-journey.mjs");
      await verifyQuickConnect(run, owner);
    } else if (flags.has("--jellyfin-accounts")) {
      const { verifyJellyfinAccounts } = await import("./lib/jellyfin-accounts-journey.mjs");
      await verifyJellyfinAccounts(run, owner);
    } else if (flags.has("--settings")) {
      await verifySettings(run, owner);
      console.log("PASS: owner/admin/user settings, independent denials, fresh-authentication refusals, desktop/mobile keyboard and axe evidence.");
    } else {
      await verifyUsers(run, owner);
      console.log("PASS: local-password routes gone; Jellyfin sign-in admitted, promoted, and persisted a member; directory denial, Jellyfin-owned profile, and fresh-password refusals held.");
      await verifyBrowserSessions(run, owner);
      console.log("PASS: browser tab identity changes, denied deep link, permission refresh, revoked session, account switch, first Jellyfin sign-in, and two concurrent contexts kept each user's state apart at 1440 and 320.");
    }
    if (flags.has("--reviews")) {
      await verifyReviews(run, owner);
      console.log("PASS: EDL upload, browser MCF upload, review rename/copy, real sidecars, deletion refusal, and fresh mobile reopen.");
    }
  } else if (mode === "upgrade") {
    await verifyUpgrade(run);
    console.log("PASS: startup upgraded synthetic 0009 activity and community history to 0030, legacy exports and sidecars intact, the unlinked owner disabled with cause unlinked, no password columns or Argon2 bytes, old cookie 401, key 403, local sign-in gone, and the sign-in screen unavailable at 1440 and 320.");
  } else if (mode === "serve") {
    await expectSetupScreen(run);
    console.log(`Ready: the Jellyfin setup screen at ${run.manifest.origin}. Drive only that origin. Run doctor with ${run.evidence}. Stop this helper with Ctrl+C or SIGTERM.`);
    await Promise.race([once(process, "SIGINT"), once(process, "SIGTERM"), once(run.child, "exit").then(() => { throw new Error("Development launcher stopped during manual verification"); })]);
  }
} catch (error) {
  run.write("failure.json", { message: error.message, stack: error.stack });
  console.error(error.message);
  process.exitCode = error.blocked ? 2 : 1;
} finally {
  try { await cleanup(run); }
  catch (error) { console.error(`Cleanup failed: ${error.message}`); process.exitCode = 1; }
  if (run.provider?.server.listening) await run.provider.close();
  try { await run.stopJellyfin?.(); }
  catch (error) { console.error(`Jellyfin cleanup failed: ${error.message}`); process.exitCode = 1; }
}
if (mode === "upgrade" && !process.exitCode) {
  const { runLinkedUpgrade } = await import("./lib/upgrade-linked-journey.mjs");
  process.exitCode = await runLinkedUpgrade(root, portRange);
}

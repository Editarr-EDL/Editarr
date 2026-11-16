import { expect, it } from "vitest";
import type { Capability } from "../auth/capabilities";
import { canDeletePublication, canDownload, exportCondition, occupant, publishDestinations } from "./export-model";
import { testPublication } from "./export-test-fixtures";

const grant = (...capabilities: Capability[]) => (capability: Capability) => capabilities.includes(capability);
const member = grant("exports.personal.download.own", "exports.personal.publish.own", "exports.personal.delete.own");
const admin = grant("exports.personal.download.own", "exports.personal.publish.own", "exports.personal.delete.own",
  "exports.personal.delete.other", "exports.default.publish", "exports.default.delete", "reviews.export.other");

it("reads each discovery state as exactly one condition", () => {
  expect(exportCondition(testPublication())).toBe("current");
  expect(exportCondition(testPublication({ current: false, outdated: true }))).toBe("outdated");
  expect(exportCondition(testPublication({ regenerating: true, regeneration_state: "queued", current: false }))).toBe("regenerating");
  expect(exportCondition(testPublication({ failed: true, regeneration_state: "failed", serving: true, current: false }))).toBe("regeneration_failed");
  expect(exportCondition(testPublication({ failed: true, regeneration_state: "failed", serving: false, content_sha256: null }))).toBe("withdrawn");
  expect(exportCondition(testPublication({ serving: false, content_sha256: null, current: false }))).toBe("deleted");
});

it("offers members only their own personal destination and nothing for another user's review", () => {
  expect(publishDestinations(4, 4, member)).toEqual([{ target: "self", userId: 4 }]);
  expect(publishDestinations(9, 4, member)).toEqual([]);
  expect(canDownload(4, 4, member)).toBe(true);
  expect(canDownload(9, 4, member)).toBe(false);
});

it("offers admins self, the review owner, and the item default for another user's review", () => {
  expect(publishDestinations(9, 1, admin)).toEqual([
    { target: "self", userId: 1 }, { target: "review_owner", userId: 9 }, { target: "item_default", userId: null },
  ]);
  expect(publishDestinations(1, 1, admin).map((destination) => destination.target)).toEqual(["self", "item_default"]);
  const noDefault = grant("exports.personal.publish.own", "reviews.export.other");
  expect(publishDestinations(9, 1, noDefault).map((destination) => destination.target)).toEqual(["self", "review_owner"]);
});

it("requires the capability each file's deletion exercises", () => {
  const own = testPublication({ target_user_id: 4 });
  const other = testPublication({ target_user_id: 9 });
  const shared = testPublication({ scope: "default", target: "item_default", target_user_id: null, target_export_id: null });
  expect([own, other, shared].map((publication) => canDeletePublication(publication, 4, member))).toEqual([true, false, false]);
  expect([own, other, shared].map((publication) => canDeletePublication(publication, 4, admin))).toEqual([true, true, true]);
});

it("finds the occupant of a destination per format without mixing users or the default", () => {
  const rows = [
    testPublication({ id: 1, target_user_id: 4 }),
    testPublication({ id: 2, target_user_id: 9 }),
    testPublication({ id: 3, format: "mcf", target_user_id: 4 }),
    testPublication({ id: 4, scope: "default", target: "item_default", target_user_id: null }),
  ];
  expect(occupant(rows, { target: "self", userId: 4 }, "edl")?.id).toBe(1);
  expect(occupant(rows, { target: "review_owner", userId: 9 }, "edl")?.id).toBe(2);
  expect(occupant(rows, { target: "self", userId: 4 }, "mcf")?.id).toBe(3);
  expect(occupant(rows, { target: "item_default", userId: null }, "edl")?.id).toBe(4);
  expect(occupant(rows, { target: "item_default", userId: null }, "mcf")).toBeUndefined();
});

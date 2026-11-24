import { describe, expect, it } from "vitest";
import type { UpdateRow } from "../api/api-community-master-types";
import { masterFilter } from "./community-master-test-fixtures";
import { acceptAllRows, bannerText, estimateText, filterActive, filterVisible, versionChoice, OTHER } from "./community-master-format";

describe("preference filtering", () => {
  const defaults = { confidenceThreshold: 0, showLowConfidence: false };
  it("hides untouched rows below the threshold and Low Confidence rows until the toggle is on", () => {
    expect(filterVisible(masterFilter({ confidence: 0.3 }), { ...defaults, confidenceThreshold: 0.35 })).toBe(false);
    expect(filterVisible(masterFilter({ confidence: 0.35 }), { ...defaults, confidenceThreshold: 0.35 })).toBe(true);
    expect(filterVisible(masterFilter({ lowConfidence: true }), defaults)).toBe(false);
    expect(filterVisible(masterFilter({ lowConfidence: true }), { ...defaults, showLowConfidence: true })).toBe(true);
  });

  it("keeps touched rows regardless of threshold and toggle, and gates activity on alignment and state", () => {
    const touched = masterFilter({ confidence: 0.05, lowConfidence: true, touched: true, state: "verified" });
    expect(filterVisible(touched, { confidenceThreshold: 0.9, showLowConfidence: false })).toBe(true);
    expect(filterActive(touched, defaults, false)).toBe(true);
    expect(filterActive(touched, defaults, true)).toBe(false);
    expect(filterActive(masterFilter({ touched: true, state: "rejected" }), defaults, false)).toBe(false);
  });
});

it("formats estimates, banner counts and version choices", () => {
  expect(estimateText({ estimates: { approximate: true, newFilters: 4, confirmations: 27, adjustments: 3, disputes: 1 } }))
    .toBe("≈4 new, ≈27 confirmations, 3 adjustments, 1 dispute");
  expect(bannerText({ new: 3, changed: 2, removed: 1, nowLowConfidence: 0 })).toBe("Community filters updated: 3 new, 2 changed, 1 removed.");
  expect(bannerText({ new: 0, changed: 0, removed: 0, nowLowConfidence: 2 })).toContain("2 now Low Confidence");
  expect(versionChoice({ selection: "", otherText: "" })).toBeNull();
  expect(versionChoice({ selection: "theatrical", otherText: "x" })).toEqual({ choice: "alias", alias_id: "theatrical" });
  expect(versionChoice({ selection: OTHER, otherText: "  IMAX " })).toEqual({ choice: "other", text: "IMAX" });
  expect(versionChoice({ selection: OTHER, otherText: " " })).toEqual({ choice: "other", text: null });
});

it("accepts all only untouched rows whose Accept is enabled", () => {
  const row = (rowId: string, touched: boolean, canAccept: boolean): UpdateRow => ({ rowId, filterId: rowId, category: "violence",
    touched, changedFields: [], mine: null, oldMaster: null, newMaster: null, mergedInto: null, allowed: ["accept", "dismiss"], canAccept });
  expect(acceptAllRows([row("a", false, true), row("b", true, true), row("c", false, false)]).map((item) => item.rowId)).toEqual(["a"]);
});

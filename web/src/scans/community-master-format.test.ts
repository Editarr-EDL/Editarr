import { describe, expect, it } from "vitest";
import { masterFilter } from "./community-master-test-fixtures";
import { MasterApiError } from "../api/api-community-masters";
import { estimateText, filterActive, filterVisible, masterErrorText, versionChoice, OTHER } from "./community-master-format";

describe("preference filtering", () => {
  const defaults = { confidenceThreshold: 0, showLowConfidence: false };
  it("hides rows below the threshold and Low Confidence rows until the toggle is on", () => {
    expect(filterVisible(masterFilter({ confidence: 0.3 }), { ...defaults, confidenceThreshold: 0.35 })).toBe(false);
    expect(filterVisible(masterFilter({ confidence: 0.35 }), { ...defaults, confidenceThreshold: 0.35 })).toBe(true);
    expect(filterVisible(masterFilter({ lowConfidence: true }), defaults)).toBe(false);
    expect(filterVisible(masterFilter({ lowConfidence: true }), { ...defaults, showLowConfidence: true })).toBe(true);
  });

  it("hides downvoted rows when asked without changing which filters start enabled", () => {
    const hide = { ...defaults, hideDownvoted: true };
    const down = masterFilter({ myVote: "down" });
    expect(filterVisible(down, hide)).toBe(false);
    expect(filterVisible(down, defaults)).toBe(true);
    expect(filterActive(down, hide, false)).toBe(filterActive(down, defaults, false));
  });

  it("starts filters enabled by threshold and Low Confidence, never while alignment is required", () => {
    const low = masterFilter({ lowConfidence: true, confidence: 0.3 });
    expect(filterActive(low, defaults, false)).toBe(false);
    expect(filterActive(low, { ...defaults, showLowConfidence: true }, false)).toBe(true);
    expect(filterActive(low, { confidenceThreshold: 0.5, showLowConfidence: true }, false)).toBe(false);
    expect(filterActive(masterFilter(), defaults, true)).toBe(false);
    const absent = masterFilter();
    expect(filterActive({ ...absent, master: { ...absent.master, present: false } }, defaults, false)).toBe(false);
  });
});

it("formats estimates and version choices", () => {
  expect(estimateText({ estimates: { approximate: true, newFilters: 4, confirmations: 27, adjustments: 3, disputes: 1 } }))
    .toBe("≈4 new, ≈27 confirmations, 3 adjustments, 1 dispute");
  expect(versionChoice({ selection: "", otherText: "" })).toBeNull();
  expect(versionChoice({ selection: "theatrical", otherText: "x" })).toEqual({ choice: "alias", alias_id: "theatrical" });
  expect(versionChoice({ selection: OTHER, otherText: "  IMAX " })).toEqual({ choice: "other", text: "IMAX" });
  expect(versionChoice({ selection: OTHER, otherText: " " })).toEqual({ choice: "other", text: null });
});

it("explains route failures, including the rate-limit wait and validation details", () => {
  expect(masterErrorText(new MasterApiError("rate_limited", true, 429, null, null, 7))).toBe("Please wait 7 seconds before trying again.");
  expect(masterErrorText(new MasterApiError("rate_limited", true, 429, null, null, 1))).toBe("Please wait 1 second before trying again.");
  expect(masterErrorText(new MasterApiError("validation_failed", false, 422, null, null, null, ["Send either anchors or offset_ms"])))
    .toBe("Some of the values aren’t valid: Send either anchors or offset_ms.");
  expect(masterErrorText(new MasterApiError("validation_failed", false, 422))).toMatch(/values aren’t valid/);
  for (const code of ["replace_required", "confirmation_required", "alignment_not_required", "alignment_out_of_range",
    "version_unavailable", "version_not_found", "report_ineligible", "vote_pending", "operation_conflict",
    "receipt_owner_mismatch", "link_unavailable", "not_connected", "rotation_pending", "not_configured", "not_found"]) {
    expect(masterErrorText(new MasterApiError(code, false, 409)), code).not.toBe("Community filters are unavailable right now.");
  }
});

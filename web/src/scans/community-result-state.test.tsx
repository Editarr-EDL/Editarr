import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { defaultResultFilters, filterResults, requiresReview, useCommunitySelection } from "./community-result-state";
import { mixedCandidate } from "../test/community-lookup-fixtures";

afterEach(cleanup);

it("keeps mixed unknown channels review-required and selects strong video only explicitly", () => {
  const { result } = renderHook(() => useCommunitySelection([mixedCandidate], "target", false));
  expect(result.current.selections).toEqual([]);
  expect(requiresReview(mixedCandidate)).toBe(true);
  act(() => result.current.selectStrong([mixedCandidate]));
  expect(result.current.selections[0].channels).toEqual(["video"]);
  act(() => result.current.change(mixedCandidate, ["video", "audio"]));
  expect(result.current.selections[0].reviewRequired).toEqual(["audio"]);
});

it("invalidates target/revision changes and refuses stale or unavailable selection", () => {
  const { result, rerender } = renderHook(({ key, disabled }) => useCommunitySelection([mixedCandidate], key, disabled),
    { initialProps: { key: "first", disabled: false } });
  act(() => result.current.change(mixedCandidate, ["audio"]));
  rerender({ key: "second", disabled: false });
  expect(result.current.selections).toEqual([]);
  rerender({ key: "second", disabled: true });
  act(() => result.current.selectStrong([mixedCandidate]));
  expect(result.current.selections).toEqual([]);
});

it("does not rank popularity ahead of compatibility and filters channel bands independently", () => {
  const popular = { ...mixedCandidate, revisionId: "popular", statistics: { ...mixedCandidate.statistics, imports: 999 },
    compatibility: { ...mixedCandidate.compatibility, video: { ...mixedCandidate.compatibility.video, band: "title_only" as const, strong_eligible: false } } };
  expect(filterResults([popular, mixedCandidate], { ...defaultResultFilters, sort: "imports" }).map((item) => item.revisionId)).toEqual(["mixed", "popular"]);
  expect(filterResults([popular, mixedCandidate], { ...defaultResultFilters, video: "strong_fingerprint", audio: "possible_cut" })).toEqual([mixedCandidate]);
  expect(filterResults([mixedCandidate], { ...defaultResultFilters, coverage: "unknown", language: "unknown", kind: "derived" })).toEqual([mixedCandidate]);
  expect(filterResults([mixedCandidate], { ...defaultResultFilters, channel: "audio", category: "language/swearing" })).toEqual([mixedCandidate]);
});

it("secondary sorts operate only within equal video/audio bands", () => {
  const newer = { ...mixedCandidate, revisionId: "newer", catalogCreatedAt: mixedCandidate.catalogCreatedAt + 1,
    attribution: { ...mixedCandidate.attribution, displayName: "A author" }, statistics: { ...mixedCandidate.statistics, imports: 1 } };
  for (const sort of ["newest", "uploader", "imports"] as const) {
    expect(filterResults([mixedCandidate, newer], { ...defaultResultFilters, sort }).map((candidate) => candidate.revisionId)).toEqual(["newer", "mixed"]);
  }
});
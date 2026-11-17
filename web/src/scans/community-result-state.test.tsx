import { expect, it } from "vitest";
import { mixedCandidate } from "../test/community-lookup-fixtures";
import { categoryCounts, communityRows, compatibleChannels, displayedBand, matchBands, scopedChannels, selectDownload } from "./community-result-state";

const all = new Set(matchBands);

it("uses the least compatible covered grade, scoped to the requested channel", () => {
  const included = communityRows([mixedCandidate], "both", all, { column: "match", direction: "ascending" });
  expect(included[0]).toMatchObject({ band: "possible_cut", count: 3, channels: ["video", "audio"] });
  expect(communityRows([mixedCandidate], "video", new Set(["strong_fingerprint"]), { column: "match", direction: "ascending" })[0])
    .toMatchObject({ band: "strong_fingerprint", count: 2, channels: ["video"] });
  expect(communityRows([mixedCandidate], "both", new Set(["strong_fingerprint"]), { column: "match", direction: "ascending" })).toEqual([]);
});

it("preserves declared coverage including zero counts and avoids inventing a grade for blank legacy coverage", () => {
  const legacy = { ...mixedCandidate, summary: { ...mixedCandidate.summary, channels: { video: 0 } } };
  expect(scopedChannels(legacy, "video")).toEqual(["video"]);
  expect(scopedChannels(legacy, "both")).toEqual([]);
  expect(displayedBand(legacy, [])).toBeNull();
});

it("filters multiple grades and sorts either direction without disturbing catalog ties", () => {
  const candidate2 = { ...mixedCandidate, revisionId: "another", summary: { ...mixedCandidate.summary, name: "Another review", total: 2,
    channels: { video: 2 } } };
  const candidates = [mixedCandidate, candidate2];
  const selected = new Set(["strong_fingerprint", "possible_cut"] as const);
  for (const column of ["match", "name", "filters"] as const) {
    expect(communityRows(candidates, "all", selected, { column, direction: "ascending" }).map(({ candidate }) => candidate.revisionId))
      .toEqual(["another", "mixed"]);
    expect(communityRows(candidates, "all", selected, { column, direction: "descending" }).map(({ candidate }) => candidate.revisionId))
      .toEqual(["mixed", "another"]);
  }
  expect(communityRows(candidates, "all", new Set(), { column: "match", direction: "ascending" })).toEqual([]);
  const tied = [{ ...mixedCandidate, revisionId: "a" }, mixedCandidate];
  expect(communityRows(tied, "all", all, { column: "match", direction: "ascending" }).map(({ candidate }) => candidate.revisionId))
    .toEqual(["a", "mixed"]);
});

it("keeps download approval scoped to the selected compatible channels", () => {
  expect(selectDownload(mixedCandidate, "target", ["video", "audio"])).toMatchObject({
    targetKey: "target", revisionId: "mixed", artifactDigest: mixedCandidate.artifactDigest,
    channels: ["video", "audio"], reviewRequired: ["audio"],
  });
  const conflict = { ...mixedCandidate, compatibility: { ...mixedCandidate.compatibility,
    audio: { ...mixedCandidate.compatibility.audio, conflicts: ["audio_stream_playback_start"] } } };
  expect(compatibleChannels(conflict, scopedChannels(conflict, "all"))).toEqual(["video"]);
});

it("totals each category once and separates its subcategories", () => {
  expect(categoryCounts({ "language/language": 2, "language/nameCalling": 3, unclassified: 1 })).toEqual([
    { label: "Language", total: 5, items: [{ label: "General", count: 2 }, { label: "Name calling", count: 3 }] },
    { label: "Unclassified", total: 1, items: [] },
  ]);
});

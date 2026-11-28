import { expect, it } from "vitest";
import { verbPhrase } from "../activity/event-row";
import { activity } from "../test-fixtures-insights";
import { ago, bytes, categoryClass, delta, filterSummary, initials, itemTitle, runtime, timecode } from "./format";

const now = Date.parse("2026-11-27T18:00:00Z");
it("formats times, sizes, changes and labels for operators", () => {
  expect([ago(now - 12000, now), ago(now - 2 * 3600000, now), ago(now - 26 * 3600000, now), ago(now - 20 * 86400000, now), ago(now + 3600000, now)])
    .toEqual(["12 s ago", "2 h ago", "Yesterday", "Nov 7", "in 1 h"]);
  expect([timecode(3751400), timecode(725000), runtime(8072000)]).toEqual(["01:02:31.4", "00:12:05", "2:14:32"]);
  expect([bytes(512), bytes(43830067), bytes(44667813888)]).toEqual(["512 B", "41.8 MB", "41.6 GB"]);
  expect([delta(46, 41)?.text, delta(29, 25, true)?.text, delta(3, 0)?.text, delta(1, null)]).toEqual(["▲ 12%", "▲ 4", "▲ 3", null]);
  expect(itemTitle({ workId: "w", title: "Example Series", year: 2019, kind: "episode", episode: { season: 1, episode: 3 } })).toBe("Example Series · S01E03");
  expect(filterSummary({ filterId: "f", category: "sexual", subcategory: "nudity", action: "skip", startMs: 0, endMs: 1500 }))
    .toBe("Sexual content › Nudity · skip · 00:00:00–00:00:01.5");
  expect(filterSummary({ filterId: "f_gone", category: null, subcategory: null, action: null, startMs: null, endMs: null })).toBe("Filter f_gone · —–—");
  expect([initials("The Quiet Orchard"), initials("kk_shift"), categoryClass("Violence"), categoryClass(null)]).toEqual(["QO", "KS", "cat-violence", "cat-other"]);
});
it("builds activity sentences from verb codes", () => {
  expect(activity.map(event => verbPhrase(event as never))).toEqual(["uploaded 14 entries to", "voted up", "voted down", "resolved a report on",
    "uploaded 9 entries to", "reported as inaccurate", "quarantined", "uploaded 29 entries to"]);
});

import { describe, expect, it } from "vitest";
import { groupAt, hasRail, jumpStatus, railEntries, railName } from "./jump-groups";

const group = (key: string, label: string, offset: number) => ({ key, label, offset });

describe("jump rail entries", () => {
  it("lists every letter under title sorting in the sort direction, without a group for absent letters", () => {
    const groups = [group("Z", "Z", 0), group("B", "B", 4), group("#", "#", 9)];
    const entries = railEntries("title", groups, "desc");
    expect(entries.map((entry) => entry.text).join("")).toBe("ZYXWVUTSRQPONMLKJIHGFEDCBA#");
    expect(entries.filter((entry) => entry.group).map((entry) => [entry.key, entry.group?.offset])).toEqual([["Z", 0], ["B", 4], ["#", 9]]);
    expect(entries.at(-1)?.name).toBe("numbers and symbols");
    expect(railName("title")).toBe("Jump to letter");
  });

  it("lists only the server's groups for other sorts, in its order, with missing values shown as a dash", () => {
    const ratings = railEntries("rating", [group(":none", "No rating", 0), group("pg-13", "PG-13", 3), group("g", "G", 8)], "desc");
    expect(ratings.map(({ text, name }) => [text, name])).toEqual([["—", "No rating"], ["PG-13", "PG-13"], ["G", "G"]]);
    // A rating actually named "None" is a rating, not the unrated group.
    expect(railEntries("rating", [group("none", "None", 0), group(":none", "No rating", 2)], "asc").map(({ text, name }) => [text, name]))
      .toEqual([["None", "None"], ["—", "No rating"]]);
    const lengths = railEntries("duration", [["0", "<90m"], ["90", "90m"], ["120", "2h"], ["150", "2h30"], ["180", "3h+"], [":none", "No length"]]
      .map(([key, label], offset) => group(key, label, offset)), "asc");
    expect(lengths.map(({ text, name }) => `${text}=${name}`)).toEqual([
      "<90m=under 90 minutes", "90m=90 minutes", "2h=2 hours", "2h30=2 hours 30 minutes", "3h+=3 hours or more", "—=No length",
    ]);
    expect(railEntries("type", [group("movie", "Movie", 0), group("series", "Series", 7)], "asc").map((entry) => entry.name))
      .toEqual(["Movie", "Series"]);
    expect(jumpStatus("year", group("2010", "2010s", 5))).toBe("Loading titles for 2010s…");
    expect(jumpStatus("title", group("#", "#", 0))).toBe("Loading titles starting with numbers and symbols…");
  });

  it("finds the group holding a listing index from the offsets", () => {
    const groups = [group("2020", "2020s", 0), group("2010", "2010s", 12), group(":none", "No year", 40)];
    expect([null, 0, 11, 12, 39, 40, 500].map((index) => groupAt(groups, index)?.key ?? null))
      .toEqual([null, "2020", "2020", "2010", "2010", ":none", ":none"]);
    expect(groupAt([], 3)).toBeNull();
  });

  it("shows a title rail for any letters but other rails only with groups to move between", () => {
    expect(hasRail("title", [group("A", "A", 0)])).toBe(true);
    expect(hasRail("type", [group("movie", "Movie", 0)])).toBe(false);
    expect(hasRail("type", [group("movie", "Movie", 0), group("series", "Series", 3)])).toBe(true);
    expect(hasRail("year", [])).toBe(false);
  });
});

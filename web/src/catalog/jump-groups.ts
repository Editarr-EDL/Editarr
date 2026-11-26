import type { LibraryJumpGroup } from "../api/api-media-types";
import type { SortDirection, SortKey } from "./catalog-types";

/** The title rail's letters in ascending order; "#" collects titles that don't start with A–Z. */
const railLetters = ["#", ..."ABCDEFGHIJKLMNOPQRSTUVWXYZ"];
const missingKey = ":none";
const lengthNames: Record<string, string> = {
  0: "under 90 minutes", 90: "90 minutes", 120: "2 hours", 150: "2 hours 30 minutes", 180: "3 hours or more",
};
const railNames: Record<SortKey, string> = {
  title: "Jump to letter", year: "Jump to decade", rating: "Jump to rating", duration: "Jump to length", type: "Jump to type",
};

/** One rail button: a group of the listing, or under title sorting a letter with no titles (`group` null). */
export interface RailEntry {
  key: string;
  /** Compact text shown on the rail. */
  text: string;
  /** Spoken name, as in "Jump to {name}". */
  name: string;
  group: LibraryJumpGroup | null;
}

/** Accessible name of a jump rail for `sort`. */
export function railName(sort: SortKey): string {
  return railNames[sort];
}

/** Spoken name of a group: "numbers and symbols" for "#", lengths in words, otherwise the server's label. */
export function groupName(sort: SortKey, group: Pick<LibraryJumpGroup, "key" | "label">): string {
  if (sort === "title" && group.key === "#") return "numbers and symbols";
  if (sort === "duration" && group.key !== missingKey) return lengthNames[group.key] ?? group.label;
  return group.label;
}

/**
 * The rail's buttons in list order. Under title sorting every letter is listed (those without titles have no
 * group, so they are shown disabled), following the sort direction; other sorts list only the server's groups.
 */
export function railEntries(sort: SortKey, groups: LibraryJumpGroup[], direction: SortDirection): RailEntry[] {
  const entry = (group: LibraryJumpGroup): RailEntry => ({
    key: group.key, text: group.key === missingKey && sort !== "title" ? "—" : group.label, name: groupName(sort, group), group,
  });
  if (sort !== "title") return groups.map(entry);
  const byLetter = new Map(groups.map((group) => [group.key, group]));
  const letters = direction === "asc" ? railLetters : [...railLetters].reverse();
  return letters.map((letter) => {
    const group = byLetter.get(letter);
    return group ? entry(group) : { key: letter, text: letter, name: groupName(sort, { key: letter, label: letter }), group: null };
  });
}

/** The group holding listing `index`: the last group starting at or before it. Groups are in offset order. */
export function groupAt(groups: LibraryJumpGroup[], index: number | null): LibraryJumpGroup | null {
  if (index === null) return null;
  let low = 0;
  let high = groups.length - 1;
  let found: LibraryJumpGroup | null = null;
  while (low <= high) {
    const middle = (low + high) >> 1;
    if (groups[middle].offset <= index) { found = groups[middle]; low = middle + 1; }
    else high = middle - 1;
  }
  return found;
}

/** Status announced while a jump's titles load. */
export function jumpStatus(sort: SortKey, group: LibraryJumpGroup): string {
  const name = groupName(sort, group);
  return sort === "title" ? `Loading titles starting with ${name}…` : `Loading titles for ${name}…`;
}

/**
 * Whether the listing has a rail: always under title sorting once it has letters, and for other sorts once
 * there are at least two groups to move between.
 */
export function hasRail(sort: SortKey, groups: LibraryJumpGroup[]): boolean {
  return groups.length > (sort === "title" ? 0 : 1);
}

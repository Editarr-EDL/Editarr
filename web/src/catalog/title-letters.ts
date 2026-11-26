import type { JellyfinMediaItem } from "../api/api-media-types";

/** The rail's letters in ascending title order; "#" collects titles that don't start with A–Z. */
export const railLetters = ["#", ..."ABCDEFGHIJKLMNOPQRSTUVWXYZ"];

/**
 * The initial a title sorts under, matching the server's title order: the sort name (or name) with accents
 * removed, upper-cased, or "#" when it doesn't start with A–Z.
 */
export function titleLetter(item: Pick<JellyfinMediaItem, "name" | "sort_name">): string {
  const initial = (item.sort_name || item.name).normalize("NFKD").replace(/\p{M}/gu, "").slice(0, 1).toUpperCase();
  return initial >= "A" && initial <= "Z" ? initial : "#";
}

/** Spoken name of a rail letter. */
export function letterName(letter: string): string {
  return letter === "#" ? "numbers and symbols" : letter;
}

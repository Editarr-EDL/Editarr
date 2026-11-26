import type { JellyfinMediaItem } from "../api/api-media-types";

/** The rail's letters in ascending title order; "#" collects titles that don't start with A–Z. */
export const railLetters = ["#", ..."ABCDEFGHIJKLMNOPQRSTUVWXYZ"];

/**
 * The initial a title sorts under, matching the server's grouping: the sort name (or name) with accents
 * removed and case folded ("ß" becomes "SS"), its first letter if A–Z, otherwise "#" (digits, symbols and
 * other scripts, which the server sorts before A).
 */
export function titleLetter(item: Pick<JellyfinMediaItem, "name" | "sort_name">): string {
  const initial = (item.sort_name || item.name).normalize("NFKD").replace(/\p{M}/gu, "").toUpperCase().slice(0, 1);
  return /^[A-Z]$/.test(initial) ? initial : "#";
}

/** Spoken name of a rail letter. */
export function letterName(letter: string): string {
  return letter === "#" ? "numbers and symbols" : letter;
}

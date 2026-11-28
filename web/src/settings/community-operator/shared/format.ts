/** Display formatting for operator pages. Times are shown in UTC so every operator sees the same clock. */
import type { FilterLabel, ItemRef, VersionRef } from "../../../api/api-community-operator-labels";

const minute = 60000, hour = 3600000, day = 86400000;
const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pad = (value: number) => String(value).padStart(2, "0");
const utcDay = (ms: number) => Math.floor(ms / day);

/** "Nov 12" (adds the year when it differs from `now`). */
export function shortDate(ms: number, now = Date.now()) {
  const date = new Date(ms), year = date.getUTCFullYear();
  return `${months[date.getUTCMonth()]} ${date.getUTCDate()}${year !== new Date(now).getUTCFullYear() ? `, ${year}` : ""}`;
}
export const clockTime = (ms: number) => { const date = new Date(ms); return `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`; };
/** "Today · 03:30", "Yesterday · 16:02" or "Nov 25 · 03:30". */
export function dayTime(ms: number, now = Date.now()) {
  const days = utcDay(now) - utcDay(ms);
  return `${days === 0 ? "Today" : days === 1 ? "Yesterday" : shortDate(ms, now)} · ${clockTime(ms)}`;
}
function spanText(span: number) {
  if (span < minute) return `${Math.floor(span / 1000)} s`;
  if (span < hour) return `${Math.floor(span / minute)} min`;
  if (span < day) return `${Math.floor(span / hour)} h`;
  return `${Math.floor(span / day)} d`;
}
/** Compact age without a suffix: "12 s", "18 h", "4 d". */
export const age = (ms: number, now = Date.now()) => spanText(Math.max(0, now - ms));
/** Relative time: "2 h ago", "Yesterday", then a date after a week. */
export function ago(ms: number | null, now = Date.now()) {
  if (ms === null) return "never";
  const span = now - ms;
  if (span < 0) return `in ${spanText(-span)}`;
  if (span < day) return `${age(ms, now)} ago`;
  if (utcDay(now) - utcDay(ms) === 1) return "Yesterday";
  return span < 7 * day ? `${Math.floor(span / day)} d ago` : shortDate(ms, now);
}
/** Time until `ms`: "in 18 h". */
export const until = (ms: number, now = Date.now()) => ms <= now ? "due now" : `in ${spanText(ms - now)}`;

/** Media timestamp "01:02:10.4" (tenths only when present). */
export function timecode(ms: number | null) {
  if (ms === null) return "—";
  const total = Math.max(0, ms) / 1000, h = Math.floor(total / 3600), m = Math.floor(total % 3600 / 60), s = total % 60;
  const whole = Math.floor(s), tenths = Math.round((s - whole) * 10);
  return `${pad(h)}:${pad(m)}:${pad(whole)}${tenths ? `.${tenths === 10 ? 9 : tenths}` : ""}`;
}
/** Runtime "2:14:32". */
export function runtime(ms: number | null) {
  if (ms === null) return "Unknown runtime";
  const total = Math.round(ms / 1000);
  return `${Math.floor(total / 3600)}:${pad(Math.floor(total % 3600 / 60))}:${pad(total % 60)}`;
}
export function bytes(value: number | null) {
  if (value === null) return "—";
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let scaled = value / 1024, index = 0;
  while (scaled >= 1024 && index < units.length - 1) { scaled /= 1024; index += 1; }
  return `${scaled >= 100 ? Math.round(scaled) : scaled.toFixed(1)} ${units[index]}`;
}
export const count = (value: number) => value.toLocaleString("en-US");
export const compact = (value: number) => value >= 10000 ? `${Math.round(value / 1000)}k` : value >= 1000 ? `${(value / 1000).toFixed(1)}k` : String(value);
export const duration = (ms: number | null) => ms === null ? "—" : ms < 60000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms / 60000)} min`;
/** Change against the previous period: "▲ 12%" (or an absolute "▲ 4" when `absolute`). */
export function delta(value: number, previous: number | null, absolute = false) {
  if (previous === null) return null;
  const change = value - previous;
  const amount = absolute || previous === 0 ? Math.abs(change) : Math.round(Math.abs(change) / previous * 100);
  return { text: `${change < 0 ? "▼" : "▲"} ${count(amount)}${absolute || previous === 0 ? "" : "%"}`, down: change < 0 };
}

/** The title, with the episode code for episodes: "Example Series · S01E03". */
export const itemName = (item: ItemRef) => item.episode ? `${item.title} · S${pad(item.episode.season)}E${pad(item.episode.episode)}` : item.title;
/** "Arrival Road (1977)", or "Example Series · S01E03" for episodes. */
export function itemTitle(item: ItemRef | null | undefined) {
  if (!item) return "Unknown item";
  return item.episode || !item.year ? itemName(item) : `${item.title} (${item.year})`;
}
export const versionName = (version: VersionRef | null | undefined) => !version ? "Unknown version" : version.label ?? version.versionId;
const categoryNames: Record<string, string> = { sexual: "Sexual content" };
export function filterName(filter: FilterLabel) {
  if (!filter.category) return `Filter ${filter.filterId}`;
  const category = categoryNames[filter.category.toLowerCase()] ?? titleCase(filter.category);
  return filter.subcategory ? `${category} › ${titleCase(filter.subcategory)}` : category;
}
export const filterSpan = (filter: FilterLabel) => `${timecode(filter.startMs)}–${timecode(filter.endMs)}`;
/** "Violence › Gore · skip · 01:02:10–01:02:31". */
export const filterSummary = (filter: FilterLabel) => [filterName(filter), filter.action, filterSpan(filter)].filter(Boolean).join(" · ");
export function titleCase(value: string) {
  const spaced = value.replace(/[_-]+/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
export function initials(name: string) {
  const words = name.replace(/^(the|a|an)\s+/i, "").split(/[\s_.-]+/).filter(word => /[\p{L}\p{N}]/u.test(word));
  return (words.length > 1 ? words[0][0] + words[1][0] : (words[0] ?? "?").slice(0, 2)).toUpperCase();
}
/** A stable small integer for `seed`, used to pick avatar and poster colour classes. */
export function hue(seed: string, buckets: number) {
  let hash = 0;
  for (const char of seed) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return hash % buckets;
}
const categories = ["language", "violence", "sexual", "substances", "frightening"];
/** CSS colour class for a filter category. */
export function categoryClass(category: string | null) {
  const value = (category ?? "").toLowerCase();
  return `cat-${categories.find(name => value.startsWith(name)) ?? "other"}`;
}
export const plural = (value: number, word: string, many = `${word}s`) => `${count(value)} ${value === 1 ? word : many}`;

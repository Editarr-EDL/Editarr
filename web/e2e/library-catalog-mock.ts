import type { Page } from "@playwright/test";
import { emptyExports, items, json, previousEdl } from "./app-fixtures";

const words = ["Arrival", "Blue", "Comet", "Dune", "Echo", "Falcon", "Glass", "Harbor", "Island", "Jade", "Kingdom", "Lantern",
  "Meadow", "North", "Ocean", "Paper", "Quiet", "River", "Silver", "Tide", "Union", "Valley", "Winter", "Xenon", "Yellow", "Zephyr"];
const ratings = ["G", "PG", "PG-13", "R"];
const resolutions = ["4K", "1080p", "720p"];
const palette = [["#2b5876", "#4e4376"], ["#614385", "#516395"], ["#134e5e", "#71b280"], ["#c94b4b", "#4b134f"], ["#373b44", "#4286f4"], ["#ee9ca7", "#ffdde1"]];

const outdatedEdl = { ...previousEdl, exports: { ...previousEdl.exports, edl: { ...previousEdl.exports.edl, current: false, outdated: true } } };

/** A movie library of `count` generated titles spread over every letter, with a few "#" titles first and every 50th without a year. */
export function generatedLibrary(count: number) {
  return Array.from({ length: count }, (_, index) => {
    const name = index < 4 ? `${2001 + index} Odyssey ${index + 1}` : `${words[index % 26]} ${["Road", "Story", "Night", "Light"][index % 4]} ${Math.floor(index / 26) + 1}`;
    const isSeries = index % 17 === 5;
    return {
      ...items[0], id: `generated-${index}`, name, sort_name: name, item_type: isSeries ? "Series" : "Movie",
      year: (index % 50 === 9 ? null : 1970 + ((index * 7) % 55)) as number | null, official_rating: ratings[index % 4] as string | null,
      resolution: resolutions[(index + Math.floor(index / 3)) % 3], duration_seconds: (4800 + index * 13) as number | null, image_url: `/api/library-items/generated-${index}/image`,
      exports: index % 3 === 0 ? previousEdl : index % 7 === 0 ? outdatedEdl : emptyExports(),
    };
  });
}

function normalized(value: string) {
  return value.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();
}

type Item = ReturnType<typeof generatedLibrary>[number];
type SortBy = "title" | "year" | "rating" | "duration" | "type";
const lengthBands: Array<[number, string]> = [[180, "3h+"], [150, "2h30"], [120, "2h"], [90, "90m"], [0, "<90m"]];

/** The server's sort value of an item, null when missing (sorted last ascending), and its jump group. */
function sortValue(item: Item, sortBy: SortBy): string | null {
  const value = { title: item.sort_name, year: item.year, rating: item.official_rating, duration: item.duration_seconds, type: item.item_type }[sortBy];
  if (value === null || value === undefined) return null;
  if (sortBy === "title") {
    const folded = normalized(String(value));
    return `${/^[a-z]/.test(folded) ? 1 : 0}${folded}`;
  }
  return typeof value === "number" ? String(value).padStart(8, "0") : normalized(value);
}
function jumpGroup(item: Item, sortBy: SortBy): { key: string; label: string } {
  if (sortBy === "title") {
    const initial = normalized(item.sort_name).slice(0, 1).toUpperCase();
    const letter = initial >= "A" && initial <= "Z" ? initial : "#";
    return { key: letter, label: letter };
  }
  if (sortBy === "type") return { key: item.item_type.toLowerCase(), label: item.item_type === "MusicVideo" ? "Music video" : item.item_type };
  const missing = { year: "No year", rating: "No rating", duration: "No length" }[sortBy];
  if (sortBy === "year") return item.year === null ? { key: "none", label: missing } : { key: String(Math.floor(item.year / 10) * 10), label: `${Math.floor(item.year / 10) * 10}s` };
  if (sortBy === "rating") return item.official_rating === null ? { key: "none", label: missing } : { key: normalized(item.official_rating), label: item.official_rating };
  if (item.duration_seconds === null) return { key: "none", label: missing };
  const [bound, label] = lengthBands.find(([minimum]) => item.duration_seconds! / 60 >= minimum)!;
  return { key: String(bound), label };
}

/**
 * Serve `library` from the movies items endpoint the way the server does: filtered, sorted, paged, with facet
 * values and jump groups. Returns the `start_index:limit` of each request, and serves generated posters.
 */
export async function mockLibraryCatalog(page: Page, library: Item[]) {
  const requests: string[] = [];
  await page.route(/\/api\/libraries\/movies\/items(\?.*)?$/, async (route) => {
    const url = new URL(route.request().url());
    const parameter = (name: string, fallback: string) => url.searchParams.get(name) ?? fallback;
    const search = normalized(parameter("search", ""));
    const edl = parameter("edl_filter", "all");
    const rating = parameter("rating_filter", "all");
    const resolution = parameter("resolution_filter", "all");
    const sortBy = parameter("sort_by", "title") as SortBy;
    const start = Number(parameter("start_index", "0"));
    const limit = Number(parameter("limit", "100"));
    requests.push(`${start}:${limit}`);
    const filtered = library.filter((item) => (!search || normalized(item.name).includes(search))
      && (edl === "all" || item.exports.exports.edl.present === (edl === "exported"))
      && (rating === "all" || item.official_rating === rating)
      && (resolution === "all" || item.resolution === resolution))
      .sort((left, right) => {
        const [first, second] = [sortValue(left, sortBy), sortValue(right, sortBy)];
        return Number(first === null) - Number(second === null) || (first ?? "").localeCompare(second ?? "")
          || normalized(left.name).localeCompare(normalized(right.name));
      });
    if (parameter("sort_direction", "asc") === "desc") filtered.reverse();
    const groups: Array<{ key: string; label: string; offset: number }> = [];
    filtered.forEach((item, offset) => {
      const group = jumpGroup(item, sortBy);
      if (groups.at(-1)?.key !== group.key) groups.push({ ...group, offset });
    });
    await json(route, {
      items: filtered.slice(start, start + limit), total_count: filtered.length,
      rating_values: [...new Set(library.map((item) => item.official_rating).filter(Boolean))], resolution_values: resolutions,
      jump_groups: groups,
    });
  });
  await page.route(/\/api\/library-items\/generated-\d+\/image$/, async (route) => {
    const index = Number(route.request().url().match(/generated-(\d+)/)?.[1] ?? 0);
    const [from, to] = palette[index % palette.length];
    const title = library[index]?.name ?? "";
    await route.fulfill({ contentType: "image/svg+xml", body: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 300"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${from}"/><stop offset="1" stop-color="${to}"/></linearGradient></defs><rect width="200" height="300" fill="url(#g)"/><text x="16" y="270" fill="#fff" font-family="sans-serif" font-weight="800" font-size="20">${title}</text></svg>` });
  });
  return requests;
}

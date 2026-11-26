import type { Page } from "@playwright/test";
import { emptyExports, items, json, previousEdl } from "./app-fixtures";

const words = ["Arrival", "Blue", "Comet", "Dune", "Echo", "Falcon", "Glass", "Harbor", "Island", "Jade", "Kingdom", "Lantern",
  "Meadow", "North", "Ocean", "Paper", "Quiet", "River", "Silver", "Tide", "Union", "Valley", "Winter", "Xenon", "Yellow", "Zephyr"];
const ratings = ["G", "PG", "PG-13", "R"];
const resolutions = ["4K", "1080p", "720p"];
const palette = [["#2b5876", "#4e4376"], ["#614385", "#516395"], ["#134e5e", "#71b280"], ["#c94b4b", "#4b134f"], ["#373b44", "#4286f4"], ["#ee9ca7", "#ffdde1"]];

const outdatedEdl = { ...previousEdl, exports: { ...previousEdl.exports, edl: { ...previousEdl.exports.edl, current: false, outdated: true } } };

/** A movie library of `count` generated titles spread over every letter, with a few "#" titles first. */
export function generatedLibrary(count: number) {
  return Array.from({ length: count }, (_, index) => {
    const name = index < 4 ? `${2001 + index} Odyssey ${index + 1}` : `${words[index % 26]} ${["Road", "Story", "Night", "Light"][index % 4]} ${Math.floor(index / 26) + 1}`;
    const isSeries = index % 17 === 5;
    return {
      ...items[0], id: `generated-${index}`, name, sort_name: name, item_type: isSeries ? "Series" : "Movie",
      year: 1970 + ((index * 7) % 55), official_rating: ratings[index % 4], resolution: resolutions[(index + Math.floor(index / 3)) % 3],
      duration_seconds: 4800 + index * 13, image_url: `/api/library-items/generated-${index}/image`,
      exports: index % 3 === 0 ? previousEdl : index % 7 === 0 ? outdatedEdl : emptyExports(),
    };
  });
}

function normalized(value: string) {
  return value.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();
}

/**
 * Serve `library` from the movies items endpoint the way the server does: filtered, sorted, paged, with facet
 * values and title letter offsets. Returns the `start_index:limit` of each request, and serves generated posters.
 */
export async function mockLibraryCatalog(page: Page, library: ReturnType<typeof generatedLibrary>) {
  const requests: string[] = [];
  await page.route(/\/api\/libraries\/movies\/items(\?.*)?$/, async (route) => {
    const url = new URL(route.request().url());
    const parameter = (name: string, fallback: string) => url.searchParams.get(name) ?? fallback;
    const search = normalized(parameter("search", ""));
    const edl = parameter("edl_filter", "all");
    const rating = parameter("rating_filter", "all");
    const resolution = parameter("resolution_filter", "all");
    const sortBy = parameter("sort_by", "title") as "title" | "year" | "rating" | "duration" | "type";
    const start = Number(parameter("start_index", "0"));
    const limit = Number(parameter("limit", "100"));
    requests.push(`${start}:${limit}`);
    const sortValue = (item: (typeof library)[number]) => ({
      title: normalized(item.sort_name), year: String(item.year).padStart(4, "0"), rating: item.official_rating,
      duration: String(item.duration_seconds).padStart(8, "0"), type: item.item_type,
    })[sortBy];
    const filtered = library.filter((item) => (!search || normalized(item.name).includes(search))
      && (edl === "all" || item.exports.exports.edl.present === (edl === "exported"))
      && (rating === "all" || item.official_rating === rating)
      && (resolution === "all" || item.resolution === resolution))
      .sort((left, right) => sortValue(left).localeCompare(sortValue(right)) || normalized(left.name).localeCompare(normalized(right.name)));
    if (parameter("sort_direction", "asc") === "desc") filtered.reverse();
    const offsets: Record<string, number> = {};
    if (sortBy === "title") filtered.forEach((item, index) => {
      const initial = normalized(item.sort_name).slice(0, 1).toUpperCase();
      const letter = initial >= "A" && initial <= "Z" ? initial : "#";
      if (offsets[letter] === undefined) offsets[letter] = index;
    });
    await json(route, {
      items: filtered.slice(start, start + limit), total_count: filtered.length,
      rating_values: ratings, resolution_values: resolutions, title_letter_offsets: offsets,
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

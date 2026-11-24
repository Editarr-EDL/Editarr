import { describe, expect, it } from "vitest";
import { mayOpen } from "./auth/route-access";
import type { Capability } from "./auth/capabilities";
import type { Principal } from "./auth/session";
import { libraryItemRoute, parseRoute } from "./routes";

describe("parseRoute", () => {
  it("reads the account deletion deep link and nothing looser", () => {
    expect(parseRoute("#/settings/users/42/delete")).toEqual({ kind: "settings", section: "users", deleteAccountId: 42 });
    expect(parseRoute("#/settings/users")).toEqual({ kind: "settings", section: "users" });
    expect(parseRoute("#/settings/users/abc/delete")).toEqual({ kind: "settings", section: "connections" });
    expect(parseRoute("#/settings/users/42")).toEqual({ kind: "settings", section: "connections" });
  });
});

it("opens a library item on a specific media source", () => {
  expect(parseRoute(`#${libraryItemRoute("movies", "movie 1", "source/2")}`))
    .toEqual({ kind: "library", libraryId: "movies", itemId: "movie 1", sourceId: "source/2" });
  expect(parseRoute(`#${libraryItemRoute("movies", "movie-1")}`)).toEqual({ kind: "library", libraryId: "movies", itemId: "movie-1" });
});

describe("Filters tabs", () => {
  const principal = (...capabilities: Capability[]) => ({ capabilities: new Set(capabilities), role: "user" } as unknown as Principal);

  it("puts the Server defaults tab in the address", () => {
    expect(parseRoute("#/settings/filters")).toEqual({ kind: "settings", section: "filters" });
    expect(parseRoute("#/settings/filters/server")).toEqual({ kind: "settings", section: "filters", filterTab: "server" });
    expect(parseRoute("#/settings/filters/other")).toEqual({ kind: "settings", section: "connections" });
  });

  it("opens the Server defaults address only with a server filter capability", () => {
    const server = parseRoute("#/settings/filters/server");
    expect(mayOpen(principal("filters.preferences.edit.own", "filters.mode.choose.own"), server)).toBe(false);
    expect(mayOpen(principal("filters.instance_mode.edit"), server)).toBe(true);
    expect(mayOpen(principal("filters.instance_defaults.edit"), server)).toBe(true);
    expect(mayOpen(principal("filters.instance_defaults.edit"), parseRoute("#/settings/filters"))).toBe(true);
  });
});

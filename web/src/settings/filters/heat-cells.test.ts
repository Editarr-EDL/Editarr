import { describe, expect, it } from "vitest";

import type { McfRegistry } from "../../api/api-mcf-types";
import { personalHeatCells, serverHeatCells } from "./heat-cells";

const registry: McfRegistry = {
  families: [
    { token: "language", label: "Language", categories: [{ token: "language", label: "Language (General)" }, { token: "swearing", label: "Swearing" }] },
    { token: "fear", label: "Fear", categories: [{ token: "arachnophobia", label: "Arachnophobia" }] },
  ],
};

describe("serverHeatCells", () => {
  it("lists every registry category in order with its default, Medium when unset", () => {
    expect(serverHeatCells(registry, [{ subcategory: "swearing", severity: "high" }])).toEqual([
      { token: "language", label: "Language (General)", family: "Language", value: "medium", server: "medium", changed: false, locked: false },
      { token: "swearing", label: "Swearing", family: "Language", value: "high", server: "high", changed: false, locked: false },
      { token: "arachnophobia", label: "Arachnophobia", family: "Fear", value: "medium", server: "medium", changed: false, locked: false },
    ]);
  });
});

describe("personalHeatCells", () => {
  it("marks overrides changed and enforced values locked while keeping known server values", () => {
    const cells = personalHeatCells(registry, [
      { subcategory: "language", severity: "low", origin: "instance", enforced: false },
      { subcategory: "swearing", severity: "medium", origin: "override", enforced: false },
      { subcategory: "arachnophobia", severity: "high", origin: "instance", enforced: true },
    ]);
    expect(cells.map(({ token, value, server, changed, locked }) => ({ token, value, server, changed, locked }))).toEqual([
      { token: "language", value: "low", server: "low", changed: false, locked: false },
      { token: "swearing", value: "medium", server: null, changed: true, locked: false },
      { token: "arachnophobia", value: "high", server: "high", changed: false, locked: true },
    ]);
  });

  it("compares copied values with the server defaults only when they are given", () => {
    const severities = [
      { subcategory: "language", severity: "low", origin: "copy", enforced: false },
      { subcategory: "swearing", severity: "high", origin: "copy", enforced: false },
    ] as const;
    const defaults = [{ subcategory: "language", severity: "low" }, { subcategory: "swearing", severity: "medium" }] as const;
    const known = personalHeatCells(registry, [...severities], [...defaults]);
    expect(known.map(({ token, server, changed }) => ({ token, server, changed }))).toEqual([
      { token: "language", server: "low", changed: false },
      { token: "swearing", server: "medium", changed: true },
      { token: "arachnophobia", server: "medium", changed: false },
    ]);
    const unknown = personalHeatCells(registry, [...severities]);
    expect(unknown.slice(0, 2).map(({ server, changed }) => ({ server, changed }))).toEqual([
      { server: null, changed: false }, { server: null, changed: false },
    ]);
  });
});

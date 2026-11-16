import { expect, it } from "vitest";
import type { ExportStatus } from "../api/api-mcf-types";
import { emptyExportStatus, legacyExports, withoutLegacyExport } from "./export-status";

function present(format: "edl" | "mcf", exportedAt: string) {
  return { ...emptyExportStatus().exports[format], present: true, exported_at: exportedAt, current: true };
}

it("deletes an adjacent sidecar without hiding the caller's own personal file", () => {
  const legacy = { ...emptyExportStatus().exports, edl: present("edl", "2026-01-01T00:00:00Z") };
  const status: ExportStatus = {
    exports: { ...legacy, edl: present("edl", "2026-10-30T00:00:00Z") },
    legacy_exports: legacy,
  };

  const after = withoutLegacyExport(status, "edl");

  expect(legacyExports(after).edl.present).toBe(false);
  expect(after.exports.edl).toEqual(status.exports.edl);
});

it("clears the compatibility badge when it was showing the adjacent sidecar", () => {
  const legacy = { ...emptyExportStatus().exports, mcf: present("mcf", "2026-01-01T00:00:00Z") };
  const after = withoutLegacyExport({ exports: legacy, legacy_exports: legacy }, "mcf");

  expect(after.exports.mcf.present).toBe(false);
  expect(legacyExports(after).mcf.present).toBe(false);
});

it("reads a personal-only status as having no adjacent sidecar to manage", () => {
  const status: ExportStatus = {
    exports: { ...emptyExportStatus().exports, edl: present("edl", "2026-10-30T00:00:00Z") },
    legacy_exports: emptyExportStatus().exports,
  };

  expect(Object.values(legacyExports(status)).some((summary) => summary.present)).toBe(false);
});

import type { EdlImportResult, McfImportResult } from "../api/api-review-types";

export type ImportResult = EdlImportResult | McfImportResult;

/** Summarize a completed import, including warnings and metadata differences. */
export function importStatusMessage(result: ImportResult): string {
  const format = "source_version" in result ? `MCF ${result.source_version}` : "EDL";
  const warning = "warning" in result && result.warning ? ` ${result.warning.message}.` : "";
  const notices = "notices" in result && result.notices.length
    ? ` Metadata differs from catalog for: ${[...new Set(result.notices.flatMap((notice) => notice.fields))].join(", ")}.`
    : "";
  return `Imported ${result.scan.name || format} as Scan #${result.scan.scan_id}.${warning}${notices}`;
}


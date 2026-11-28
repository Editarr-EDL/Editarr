import type { EdlImportResult, McfImportResult, VideoSkipImportResult } from "../api/api-review-types";

export type ImportResult = EdlImportResult | McfImportResult | VideoSkipImportResult;

/** Summarize a completed import, including warnings and metadata differences. */
export function importStatusMessage(result: ImportResult): string {
  if ("issue_count" in result) {
    const firstIssue = result.issues[0];
    const issues = result.issue_count
      ? ` ${result.issue_count} import ${result.issue_count === 1 ? "issue" : "issues"}.${firstIssue ? ` Line ${firstIssue.line_number}: ${firstIssue.message}.` : ""}`
      : "";
    return `Imported VideoSkip as Scan #${result.scan.scan_id}. Verify timing for this version.${issues}`;
  }
  const format = "source_version" in result ? `MCF ${result.source_version}` : "EDL";
  const warning = "warning" in result && result.warning ? ` ${result.warning.message}.` : "";
  const notices = "notices" in result && result.notices.length
    ? ` Metadata differs from catalog for: ${[...new Set(result.notices.flatMap((notice) => notice.fields))].join(", ")}.`
    : "";
  return `Imported ${result.scan.name || format} as Scan #${result.scan.scan_id}.${warning}${notices}`;
}


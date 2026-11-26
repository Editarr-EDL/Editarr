import type { Scan } from "../api/api-scan-types";
import type { ComponentProps } from "react";
import { PreviousVersionScans } from "../scans/previous-version-scans";
import { ScanSelectionList } from "../scans/scan-components";

/** The Scans tab's history for one item version: its ready scans, load errors, and the
 * previous versions, pending imports and linked scans that can be verified for it. */
export function MediaScanHistory({ error, readyScans, previousVersions, pendingImports, linkedScans, linkedError,
  currentVersion, canVerify, onRetry, onDeleteScan, onVerify }: {
  error: string | null; readyScans: Scan[]; previousVersions: ComponentProps<typeof PreviousVersionScans>["versions"]; pendingImports: Scan[];
  linkedScans: Scan[]; linkedError?: string; currentVersion?: string; canVerify: boolean;
  onRetry: () => void; onDeleteScan: (scan: Scan) => void; onVerify: (scan: Scan) => void;
}) {
  return <>
    {error
      ? <div className="load-error" role="alert"><span>{error}</span>
        <button type="button" className="btn-secondary" onClick={onRetry}>Retry</button></div>
      : readyScans.length ? <ScanSelectionList scans={readyScans} onDeleteScan={onDeleteScan} onRenamed={onRetry} />
        : <p className="muted-copy">{previousVersions.length || pendingImports.length || linkedScans.length ? "No verified scans for this version." : "No scans yet."}</p>}
    {linkedError && <p role="alert">{linkedError} <button type="button" className="btn-secondary compact-button"
      onClick={onRetry}>Retry</button></p>}
    <PreviousVersionScans versions={previousVersions} imports={pendingImports} linkedScans={linkedScans}
      verifiedCopies={readyScans.filter((scan) => scan.status === "complete" && !!scan.verified_from_scan_id
        && !!currentVersion && scan.stable_version_id === currentVersion)} canVerify={canVerify} onVerify={onVerify} />
  </>;
}

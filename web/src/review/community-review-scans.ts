import type { MasterTarget } from "../api/api-community-master-types";
import type { Scan } from "../api/api-scan-types";

/** Restrict the review picker, while leaving immutable history/provenance unchanged. */
export function communityReviewScans(scans: Scan[], target: MasterTarget): Scan[] {
  return scans.flatMap((scan) => {
    if (!scan.community_provenance) return [scan];
    const approval = scan.community_approvals?.find((value) => value.source === target.source && value.target_item_id === target.item_id
      && (value.media_source_id || null) === (target.media_source_id || null));
    if (!approval) return [];
    return [{ ...scan, scan_audio: scan.scan_audio && approval.channels.includes("audio"), scan_video: scan.scan_video && approval.channels.includes("video") }];
  });
}
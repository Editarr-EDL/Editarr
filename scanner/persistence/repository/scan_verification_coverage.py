"""Project source-cue coverage for verified scan copies."""

from collections import defaultdict

from scanner.db import ScanFindingRow
from scanner.modeling.model_scans import ScanVerificationCoverage

from .version_reuse import approved_reuse, cue_channels


def verification_coverage_by_scan(db, scans):
    """Read original cue identities once for all derived scans in a response."""
    approvals = {}
    for scan in scans:
        approval = approved_reuse(scan)
        if (
            approval
            and isinstance(approval.get("source_scan_id"), str)
            and isinstance(approval.get("cues"), list)
        ):
            approvals[scan.id] = approval
    if not approvals:
        return {}

    originals = {approval["source_scan_id"] for approval in approvals.values()}
    findings_by_scan = defaultdict(dict)
    rows = db.query(
        ScanFindingRow.scan_id, ScanFindingRow.id, ScanFindingRow.source, ScanFindingRow.mcf_channel
    ).filter(ScanFindingRow.scan_id.in_(originals))
    for scan_id, finding_id, source, channel in rows:
        findings_by_scan[scan_id][finding_id] = cue_channels(source, channel)
    return {
        scan_id: _coverage(approval, findings_by_scan[approval["source_scan_id"]])
        for scan_id, approval in approvals.items()
    }


def _coverage(approval, findings):
    counts = {"audio": 0, "video": 0}
    covered = {"audio": set(), "video": set()}
    for channels in findings.values():
        for channel in channels:
            counts[channel] += 1
    for cue in approval["cues"]:
        if not isinstance(cue, dict) or type(cue.get("source_finding_id")) is not int:
            continue
        finding_id = cue["source_finding_id"]
        channel = cue.get("channel")
        for component in findings.get(finding_id, ()):
            if channel == component or channel == "both":
                covered[component].add(finding_id)
    return ScanVerificationCoverage(
        source_counts=counts,
        covered_finding_ids={channel: sorted(ids) for channel, ids in covered.items()},
    )

import type { SqlDatabase } from '../runtime/contracts';
import { base64, sha256Hex, type ReceiptSigner } from '../security/service-signer';
import type { AuthenticatedContributor } from '../security/contributor-auth';
import { admitRate, RouteError } from '../http/contributor-route';
import { issueMasterReceipt, signSnapshot } from './signing';

export interface MasterDownloadOptions { service: string; signer: ReceiptSigner; versionChecksPerHour: number; now: number }

/** Parse `?knownRevision=N` (§7.3): at most one canonical non-negative decimal, and no other parameter. */
export function knownRevision(params: URLSearchParams): number | null {
  const keys = [...params.keys()];
  if (keys.some(key => key !== 'knownRevision') || keys.length > 1) throw new RouteError(400, 'invalid_master_request');
  const value = params.get('knownRevision');
  if (value === null) return null;
  if (!/^(0|[1-9][0-9]{0,15})$/.test(value) || !Number.isSafeInteger(Number(value))) throw new RouteError(400, 'invalid_master_request');
  return Number(value);
}

/** The current state of a downloadable version, or the §7.3 error for a missing, merged or quarantined one. */
export async function availableVersion(db: SqlDatabase, versionId: string) {
  const version = await db.prepare('SELECT status, merged_into, quarantined, revision FROM versions WHERE version_id = ?')
    .bind(versionId).first<{ status: string; merged_into: string | null; quarantined: number; revision: number }>();
  if (!version) throw new RouteError(404, 'version_not_found');
  if (version.status === 'merged_into') throw new RouteError(409, 'version_merged', { mergedInto: version.merged_into });
  if (version.quarantined === 1) throw new RouteError(410, 'version_unavailable');
  return version;
}

/** Filter IDs a receipt makes eligible: each served filter and the retired IDs in its `mergedIds` (§7.4). */
export function snapshotFilterIds(bytes: Uint8Array): string[] {
  const snapshot = JSON.parse(new TextDecoder().decode(bytes)) as { filters: { filterId: string; mergedIds: string[] }[] };
  return [...new Set(snapshot.filters.flatMap(filter => [filter.filterId, ...filter.mergedIds]))];
}

/**
 * Serve `GET /v1/versions/{versionId}/master` (§7.3). When `knownRevision` is current the reply is
 * `changed: false` and no receipt is issued. Otherwise the stored snapshot is signed and a receipt
 * bound to the caller, version, revision and digest is recorded with its eligible filter IDs.
 */
export async function downloadMaster(db: SqlDatabase, identity: AuthenticatedContributor, versionId: string, known: number | null,
  options: MasterDownloadOptions) {
  await admitRate(db, `master:${identity.contributorId}`, options.versionChecksPerHour, 3600, options.now);
  const version = await availableVersion(db, versionId);
  if (known !== null && known >= version.revision) return { version: 1, changed: false, versionId, revision: version.revision };
  const stored = await db.prepare('SELECT snapshot_digest, snapshot_bytes FROM master_revisions WHERE version_id = ? AND revision = ?')
    .bind(versionId, version.revision).first<{ snapshot_digest: string; snapshot_bytes: ArrayBuffer | null }>();
  if (!stored?.snapshot_bytes) throw new Error('master_snapshot_unavailable');
  const bytes = new Uint8Array(stored.snapshot_bytes);
  if (await sha256Hex(bytes) !== stored.snapshot_digest) throw new Error('master_snapshot_corrupt');
  const binding = { service: options.service, versionId, revision: version.revision, snapshotDigest: stored.snapshot_digest };
  const snapshotSignature = await signSnapshot(options.signer, binding, options.now);
  const receipt = await issueMasterReceipt(options.signer, { ...binding, contributorId: identity.contributorId }, options.now);
  await db.batch([
    db.prepare(`INSERT INTO download_receipts (receipt_id, contributor_id, version_id, revision, snapshot_digest, service_key_id, issued_at)
      VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(receipt_id) DO NOTHING`).bind(receipt.receipt.receiptId, identity.contributorId,
      versionId, version.revision, stored.snapshot_digest, options.signer.keyId, options.now),
    db.prepare(`INSERT INTO master_revision_filters (version_id, revision, filter_id) SELECT ?, ?, value FROM json_each(?) WHERE true
      ON CONFLICT DO NOTHING`).bind(versionId, version.revision, JSON.stringify(snapshotFilterIds(bytes))),
  ]);
  return { version: 1, changed: true, snapshot: base64(bytes), snapshotSignature, receipt };
}

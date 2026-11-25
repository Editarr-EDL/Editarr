import { createHash } from 'node:crypto';
import { pluralityPrior, type Cluster } from './clustering';
import type { PreparedEntry } from './counted';
import { compareAscii } from './stats';
import type { RegistryEntry } from './types';

const encoder = new TextEncoder();

/** `framed(domain, fields)`: ASCII domain, a zero byte, then u32be-length-prefixed UTF-8 fields. */
export function framed(domain: string, fields: readonly string[]) {
  const parts = [encoder.encode(`${domain}\0`)];
  for (const field of fields) {
    const bytes = encoder.encode(field), length = new Uint8Array(4);
    new DataView(length.buffer).setUint32(0, bytes.length);
    parts.push(length, bytes);
  }
  return Buffer.concat(parts);
}

/** Derive a new filter ID from a cluster's first canonical member (§6.6 step 3). */
export function deriveFilterId(versionId: string, submissionDigest: string, entryId: string, extra: readonly string[] = []) {
  const digest = createHash('sha256')
    .update(framed('editarr-community-filter-id-v1', [versionId, submissionDigest, entryId, ...extra])).digest('hex');
  return `f_${digest.slice(0, 32)}`;
}

export function compareClusters(a: Cluster, b: Cluster) {
  return a.medianStart - b.medianStart || a.medianEnd - b.medianEnd || compareAscii(a.category, b.category) || a.index - b.index;
}

/**
 * Assign stable IDs to clusters and retire unclaimed registry IDs (§6.6).
 * `duplicates` are repeat-contributor entries that count as former members for `mergedInto`.
 */
export function assignFilterIds(versionId: string, clusters: readonly Cluster[], duplicates: ReadonlyMap<PreparedEntry, Cluster>,
  registry: readonly RegistryEntry[], nextCreatedSeq?: number) {
  const rows = new Map(registry.map(row => [row.filterId, { ...row, incumbentExpression: row.incumbentExpression ?? null }]));
  const rank = new Map(registry.map(row => [row.filterId, row.createdSeq]));
  let seq = nextCreatedSeq ?? registry.reduce((max, row) => Math.max(max, row.createdSeq), 0) + 1;
  const ordered = [...clusters].sort(compareClusters);
  const ids = new Map<Cluster, string>(), claimed = new Map<string, Cluster>();
  for (const cluster of ordered) {
    let id = pluralityPrior(cluster.priorCounts, rank, candidate => !claimed.has(candidate));
    if (id === null) id = newId(versionId, cluster, claimed);
    if (!rows.has(id)) {
      rows.set(id, { filterId: id, createdSeq: seq++, incumbentSubcategory: null, incumbentAction: null,
        incumbentExpression: null, state: 'active', mergedInto: null, quarantined: false });
    }
    ids.set(cluster, id); claimed.set(id, cluster);
  }
  const formerMembers = new Map<string, Map<string, number>>();
  const countFormer = (entry: PreparedEntry, cluster: Cluster) => {
    const prior = entry.entry.priorFilterId;
    if (prior === null || claimed.has(prior) || !rows.has(prior)) return;
    const tally = formerMembers.get(prior) ?? new Map<string, number>();
    tally.set(ids.get(cluster)!, (tally.get(ids.get(cluster)!) ?? 0) + 1);
    formerMembers.set(prior, tally);
  };
  for (const cluster of clusters) for (const member of cluster.members) countFormer(member, cluster);
  for (const [entry, cluster] of duplicates) countFormer(entry, cluster);
  for (const row of rows.values()) {
    const quarantined = row.quarantined || row.state === 'quarantined';
    if (claimed.has(row.filterId)) {
      Object.assign(row, { state: quarantined ? 'quarantined' : 'active', mergedInto: null, quarantined });
      continue;
    }
    row.state = 'retired'; row.quarantined = quarantined;
    const tally = formerMembers.get(row.filterId);
    if (!tally) continue;
    const total = [...tally.values()].reduce((sum, count) => sum + count, 0);
    row.mergedInto = [...tally].find(([, count]) => count * 2 > total)?.[0] ?? null;
  }
  compressMergeChains(rows, claimed);
  const resolve = (id: string | null) => {
    if (id === null) return null;
    if (claimed.has(id)) return id;
    const target = rows.get(id)?.mergedInto ?? null;
    return target !== null && claimed.has(target) ? target : null;
  };
  const mergedIds = new Map<string, string[]>();
  for (const row of rows.values()) {
    if (row.state !== 'retired' || row.mergedInto === null || !claimed.has(row.mergedInto)) continue;
    mergedIds.set(row.mergedInto, [...(mergedIds.get(row.mergedInto) ?? []), row.filterId]);
  }
  for (const list of mergedIds.values()) list.sort(compareAscii);
  return { ids, rows, resolve, mergedIds };
}

/** First derived ID not claimed by an earlier cluster, trying members in canonical order. */
function newId(versionId: string, cluster: Cluster, claimed: ReadonlyMap<string, Cluster>) {
  for (const member of cluster.members) {
    const id = deriveFilterId(versionId, member.submission.input.digest, member.entry.entryId);
    if (!claimed.has(id)) return id;
  }
  const first = cluster.members[0];
  for (let attempt = 1; ; attempt++) {
    const id = deriveFilterId(versionId, first.submission.input.digest, first.entry.entryId, [String(attempt)]);
    if (!claimed.has(id)) return id;
  }
}

/** Point retired IDs at the current filter their merge chain ends in, when it ends in one. */
function compressMergeChains(rows: Map<string, RegistryEntry>, claimed: ReadonlyMap<string, Cluster>) {
  for (const row of rows.values()) {
    if (row.state !== 'retired' || row.mergedInto === null) continue;
    const seen = new Set([row.filterId]);
    let target: string | null = row.mergedInto;
    while (target !== null && !claimed.has(target) && !seen.has(target)) {
      seen.add(target); target = rows.get(target)?.mergedInto ?? null;
    }
    if (target !== null && claimed.has(target)) row.mergedInto = target;
  }
}

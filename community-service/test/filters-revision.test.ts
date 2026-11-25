import { describe, expect, it } from 'vitest';
import {
  assertSnapshotSize, DEFAULT_MASTER_POLICY, mergeVersion, parseMasterPolicy, SNAPSHOT_MAX_BYTES, snapshotBytes,
} from '../src/filters';
import { HEADER, persist, workedExample } from './helpers/filters';

describe('revision content (§6.9)', () => {
  const state = persist(workedExample(), mergeVersion(workedExample()));

  it('includes the snapshot header, so header changes bump the revision and identical recomputes do not', () => {
    expect(mergeVersion(state).revision).toEqual({ revision: 1, changed: false });
    const relabeled = { ...state, header: { ...HEADER, versionInfo: { ...HEADER.versionInfo, status: 'candidate' as const } } };
    expect(mergeVersion(relabeled).revision).toEqual({ revision: 2, changed: true });
    const retitled = { ...state, header: { ...HEADER, work: { ...HEADER.work, year: 2000 } } };
    expect(mergeVersion(retitled).revision).toEqual({ revision: 2, changed: true });
    expect(mergeVersion({ ...state, header: { ...HEADER, service: 'https://other.example' } }).revision.changed).toBe(true);
  });

  it('holds the revision while the version is quarantined and does not bump on an unchanged restore', () => {
    const quarantine = (value: boolean) => ({ ...state.moderation, versionQuarantined: value });
    const quarantined = mergeVersion({ ...state, moderation: quarantine(true) });
    expect(quarantined.revision).toEqual({ revision: 1, changed: false });
    expect(quarantined.servedFilters).toEqual([]);
    expect(quarantined.servedContent).toBe(state.previous!.servedContent);
    const restored = mergeVersion({ ...persist(state, quarantined), moderation: quarantine(false) });
    expect(restored.revision).toEqual({ revision: 1, changed: false });
  });

  it('builds snapshot bytes in contract key order and enforces the size limit', () => {
    const result = mergeVersion(state);
    const snapshot = JSON.parse(new TextDecoder().decode(snapshotBytes(result.servedContent, 13, 1_795_553_600)));
    expect(Object.keys(snapshot)).toEqual(['schemaVersion', 'service', 'versionId', 'revision', 'generatedAt', 'work', 'versionInfo', 'filters']);
    expect(Object.keys(snapshot.versionInfo)).toEqual(['status', 'aliasId', 'label', 'labelText', 'runtimeMs', 'trust']);
    expect(snapshot).toMatchObject({ schemaVersion: 'community-master-snapshot-1', revision: 13, versionInfo: { runtimeMs: 8_160_512 } });
    expect(snapshot.filters[0].confidence).toBe(0.354197347);
    expect(() => assertSnapshotSize(new Uint8Array(SNAPSHOT_MAX_BYTES))).not.toThrow();
    expect(() => assertSnapshotSize(new Uint8Array(SNAPSHOT_MAX_BYTES + 1))).toThrow('snapshot_too_large');
  });
});

describe('MASTER_POLICY parsing (§8.1)', () => {
  it('accepts the defaults and requires every key with valid types and ranges', () => {
    expect(parseMasterPolicy(JSON.parse(JSON.stringify(DEFAULT_MASTER_POLICY)))).toEqual(DEFAULT_MASTER_POLICY);
    const { confidenceFloor: _, ...missing } = DEFAULT_MASTER_POLICY;
    expect(() => parseMasterPolicy(missing)).toThrow();
    expect(() => parseMasterPolicy({ ...DEFAULT_MASTER_POLICY, extra: 1 })).toThrow();
    expect(() => parseMasterPolicy({ ...DEFAULT_MASTER_POLICY, matchToleranceMs: 0.5 })).toThrow();
    expect(() => parseMasterPolicy({ ...DEFAULT_MASTER_POLICY, confidenceFloor: 1.5 })).toThrow();
    expect(() => parseMasterPolicy({ ...DEFAULT_MASTER_POLICY, severityWeights: { low: 1, medium: 1 } })).toThrow();
    expect(() => parseMasterPolicy({ ...DEFAULT_MASTER_POLICY, alignmentMaxScaleDeviation: 0.2 })).toThrow();
    expect(() => parseMasterPolicy({ ...DEFAULT_MASTER_POLICY, maxServedFilters: '4000' })).toThrow();
    expect(parseMasterPolicy({ ...DEFAULT_MASTER_POLICY, maxServedFilters: 4000 }).maxServedFilters).toBe(4000);
    expect(() => parseMasterPolicy({ ...DEFAULT_MASTER_POLICY, maxServedFilters: 4001 })).toThrow();
  });
});

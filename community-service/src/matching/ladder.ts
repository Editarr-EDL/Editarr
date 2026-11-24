import { KEY_TIER, type EvidenceKeyType } from '../versions/keys';
import { labelsEqual, type Label } from '../versions/labels';

export type RuntimeAgreement = 'same_cut' | 'possible_cut' | 'not_applicable' | 'unknown' | 'outside';
export type MatchTier = '1' | '2' | '3' | '4' | '5' | 'runtime_mismatch' | 'other_version';
export type MatchKeyType = EvidenceKeyType | 'tmdb' | 'tvdb' | 'title_year';
export type VersionStatus = 'confirmed' | 'candidate';

/** Protocol constants from §4; not operator configuration. */
export const SAME_CUT_MS = 2_000;
export const POSSIBLE_CUT_MS = 30_000;
export const MATCH_PERCENT: Record<MatchTier, number> = {
  1: 100, 2: 80, 3: 65, 4: 30, 5: 15, runtime_mismatch: 5, other_version: 0,
};
const KEY_ORDER: MatchKeyType[] = ['sha256_full', 'sha256_sampled', 'md5_quick', 'audio_stream', 'video_stream',
  'tmdb', 'tvdb', 'title_year'];
const RUNTIME_ORDER: RuntimeAgreement[] = ['same_cut', 'possible_cut', 'not_applicable', 'unknown', 'outside'];

/** Compare a query runtime with a version runtime. A missing runtime on either side is `unknown`. */
export function runtimeAgreement(queryMs: number | null, versionMs: number | null): RuntimeAgreement {
  if (queryMs === null || versionMs === null) return 'unknown';
  const difference = Math.abs(queryMs - versionMs);
  return difference <= SAME_CUT_MS ? 'same_cut' : difference <= POSSIBLE_CUT_MS ? 'possible_cut' : 'outside';
}

/** What a query shares with one version: equal tier 1–3 key types and work identity keys. */
export interface VersionEvidence {
  evidenceTypes: ReadonlySet<EvidenceKeyType>;
  providerTypes: ReadonlySet<'tmdb' | 'tvdb'>;
  titleMatched: boolean;
}
export interface LadderVersion { label: Label; runtimeMs: number | null }
export interface LadderScore {
  tier: MatchTier; matchPercent: number; matchedKeys: MatchKeyType[]; runtimeAgreement: RuntimeAgreement;
}

const ordered = (types: Iterable<MatchKeyType>) => [...new Set(types)].sort((a, b) => KEY_ORDER.indexOf(a) - KEY_ORDER.indexOf(b));

/**
 * Score one version on the §4 ladder. Only the best tier counts and a mismatching key never
 * lowers it. Returns null when the query shares no key with the version.
 */
export function scoreVersion(evidence: VersionEvidence, version: LadderVersion, queryLabel: Label,
  queryRuntimeMs: number | null): LadderScore | null {
  for (const tier of [1, 2, 3] as const) {
    const matched = [...evidence.evidenceTypes].filter(type => KEY_TIER[type] === tier);
    if (matched.length) return { tier: String(tier) as MatchTier, matchPercent: MATCH_PERCENT[tier],
      matchedKeys: ordered(matched), runtimeAgreement: 'not_applicable' };
  }
  const identity: MatchKeyType[] = [...evidence.providerTypes, ...(evidence.titleMatched ? ['title_year' as const] : [])];
  if (!identity.length) return null;
  const agreement = runtimeAgreement(queryRuntimeMs, version.runtimeMs);
  const result = (tier: MatchTier, keys: MatchKeyType[]): LadderScore =>
    ({ tier, matchPercent: MATCH_PERCENT[tier], matchedKeys: ordered(keys), runtimeAgreement: agreement });
  if (!labelsEqual(queryLabel, version.label)) return result('other_version', identity);
  const inTolerance = agreement === 'same_cut' || agreement === 'possible_cut';
  if (inTolerance && evidence.providerTypes.size) return result('4', [...evidence.providerTypes]);
  if (inTolerance) return result('5', ['title_year']);
  return result('runtime_mismatch', identity);
}

export interface RankedMatch extends LadderScore { versionId: string; status: VersionStatus; trust: number }

const ascii = (a: string, b: string) => a < b ? -1 : a > b ? 1 : 0;

/** §4 result order: Match %, confirmed first, runtime agreement, trust, then version ID. */
export function compareMatches(left: RankedMatch, right: RankedMatch): number {
  return right.matchPercent - left.matchPercent ||
    Number(left.status !== 'confirmed') - Number(right.status !== 'confirmed') ||
    RUNTIME_ORDER.indexOf(left.runtimeAgreement) - RUNTIME_ORDER.indexOf(right.runtimeAgreement) ||
    right.trust - left.trust || ascii(left.versionId, right.versionId);
}

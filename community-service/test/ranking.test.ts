import { expect, it } from 'vitest';
import { rankRevision, RANKING_POLICY_VERSION, SCORE_SCALE } from '../src/reputation/ranking';
import contract from '../../tests/fixtures/community/ranking-v1.json';
import { compareCandidates } from '../src/catalog/search';
import { POLICY_VERSION, type Compatibility } from '../src/catalog/compatibility-inputs';

it('matches the approved new, sparse, positive and negative worked examples', () => {
  expect(RANKING_POLICY_VERSION).toBe(contract.policyVersion);
  expect(SCORE_SCALE).toBe(contract.scoreScale);
  for (const example of contract.cases) {
    const ranking = rankRevision(example.likes, example.dislikes);
    expect(ranking.score).toBe(example.score);
    expect(ranking.qualityScore).toBe(ranking.score);
    expect(ranking.uploaderBoost).toBe(0);
    expect(ranking.voteCount).toBe(example.likes + example.dislikes);
  }
});

it('is bounded and monotonic without volume, author, parent or age inputs', () => {
  for (let likes = 0; likes < 100; likes++) {
    for (let dislikes = 0; dislikes < 20; dislikes++) {
      const score = rankRevision(likes, dislikes).score;
      expect(score).toBeGreaterThanOrEqual(0);
      expect(score).toBeLessThanOrEqual(1);
      expect(rankRevision(likes + 1, dislikes).score).toBeGreaterThanOrEqual(score);
      expect(rankRevision(likes, dislikes + 1).score).toBeLessThanOrEqual(score);
    }
  }
  expect(rankRevision(Number.MAX_SAFE_INTEGER - 2, 0).score).toBeLessThanOrEqual(1);
});

it('rejects corrupt or unrepresentable counts rather than fabricating a rank', () => {
  for (const value of [-1, 0.5, NaN, Infinity, Number.MAX_SAFE_INTEGER]) {
    expect(() => rankRevision(value, 0)).toThrow('invalid_ranking_counts');
    expect(() => rankRevision(0, value)).toThrow('invalid_ranking_counts');
  }
});

function candidate(revisionId: string, likes: number, dislikes: number,
  video: Compatibility['video']['band'] = 'strong_fingerprint', audio: Compatibility['audio']['band'] = 'strong_fingerprint') {
  const channel = (band: Compatibility['video']['band']) => ({ band, reasons: [], conflicts: [], missing: [], strong_eligible: false });
  return { revisionId, ranking: rankRevision(likes, dislikes), compatibility: { policy_version: POLICY_VERSION,
    selected: false as const, video: channel(video), audio: channel(audio) } };
}

it('keeps both compatibility bands ahead of popularity, including wrong-cut popular scans', () => {
  const stronger = candidate('new-compatible', 0, 0);
  expect(compareCandidates(stronger, candidate('popular-wrong-video', 1000000, 0, 'possible_cut'))).toBeLessThan(0);
  expect(compareCandidates(stronger, candidate('popular-wrong-audio', 1000000, 0, 'strong_fingerprint', 'possible_cut'))).toBeLessThan(0);
  expect(compareCandidates(candidate('negative-compatible', 0, 100), candidate('popular-title', 1000000, 0, 'title_only'))).toBeLessThan(0);
});

it('ranks few votes conservatively and uses ASCII revision IDs for exact ties', () => {
  const ordered = [candidate('new', 0, 0), candidate('negative', 0, 10), candidate('sparse', 1, 0), candidate('established', 10, 0)]
    .sort(compareCandidates).map(item => item.revisionId);
  expect(ordered).toEqual(['established', 'sparse', 'new', 'negative']);
  expect([candidate('z-parent', 0, 0), candidate('a-derived', 0, 0), candidate('b-revised', 0, 0)]
    .sort(compareCandidates).map(item => item.revisionId)).toEqual(['a-derived', 'b-revised', 'z-parent']);
  expect(candidate('derived', 0, 0).ranking).toEqual(candidate('prolific-author-new-upload', 0, 0).ranking);
});
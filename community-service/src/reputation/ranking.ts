export const RANKING_POLICY_VERSION = 'community-ranking-wilson-1';
export const SCORE_SCALE = 1_000_000_000;

export interface Ranking {
  policyVersion: typeof RANKING_POLICY_VERSION;
  qualityScore: number;
  voteCount: number;
  uploaderBoost: 0;
  score: number;
}

/** Score one revision using its votes only, with one positive and negative prior. */
export function rankRevision(likes: number, dislikes: number): Ranking {
  if (![likes, dislikes].every(value => Number.isSafeInteger(value) && value >= 0) ||
      !Number.isSafeInteger(likes + dislikes + 2)) throw new Error('invalid_ranking_counts');
  const samples = likes + dislikes + 2, proportion = (likes + 1) / samples, zSquared = 1.96 ** 2;
  const lower = (proportion + zSquared / (2 * samples) - 1.96 *
    Math.sqrt(proportion * (1 - proportion) / samples + zSquared / (4 * samples ** 2))) /
    (1 + zSquared / samples);
  const qualityScore = Math.round(Math.max(0, Math.min(1, lower)) * SCORE_SCALE) / SCORE_SCALE;
  return { policyVersion: RANKING_POLICY_VERSION, qualityScore, voteCount: likes + dislikes,
    uploaderBoost: 0, score: qualityScore };
}
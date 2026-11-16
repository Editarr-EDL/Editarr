"""Versioned revision-only ranking used to verify public service scores."""

import math

RANKING_POLICY_VERSION = "community-ranking-wilson-1"
SCORE_SCALE = 1_000_000_000


def rank_revision(likes: int, dislikes: int) -> dict:
    """Compute the Wilson lower bound with one positive and negative prior vote."""
    if any(type(value) is not int or value < 0 for value in (likes, dislikes)) or (
        likes + dislikes + 2 > (1 << 53) - 1
    ):
        raise ValueError("invalid_ranking_counts")
    samples = likes + dislikes + 2
    proportion = (likes + 1) / samples
    z_squared = 1.96**2
    lower = (
        proportion
        + z_squared / (2 * samples)
        - 1.96 * math.sqrt(proportion * (1 - proportion) / samples + z_squared / (4 * samples**2))
    ) / (1 + z_squared / samples)
    score = math.floor(max(0, min(1, lower)) * SCORE_SCALE + 0.5) / SCORE_SCALE
    return {
        "policyVersion": RANKING_POLICY_VERSION,
        "qualityScore": score,
        "voteCount": likes + dislikes,
        "uploaderBoost": 0,
        "score": score,
    }

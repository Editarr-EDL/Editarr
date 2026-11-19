"""Prompt policies and family routing for the Shieldstral yes/no safety classifier.

Screens decide whether a frame is flagged at all. Routes run only on frames whose
sexual screen fired and choose which MCF families the flag is reported under. Add,
drop, or merge rows here; every threshold is a setting so it can be tuned without
code changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from scanner.models import McfCategoryFamily
from scanner.settings.config import settings

# Shieldstral's documented classifier system prompt. The model was trained on this text.
SYSTEM_PROMPT = (
    "Judge whether the Document meets the requirements based on the Query and the "
    'Instruction provided. Note that the answer can only be "yes" or "no".'
)
INSTRUCT = (
    "You moderate still frames from movies for a family content filter. "
    "Apply a strict standard."
)
NUDITY_LABEL = "shieldstral_nudity"
SEXUAL_LABEL = "shieldstral_sexual"
VIOLENCE_LABEL = "shieldstral_violence"
LABEL_FAMILIES = {
    NUDITY_LABEL: McfCategoryFamily.NUDITY,
    SEXUAL_LABEL: McfCategoryFamily.SEX,
    VIOLENCE_LABEL: McfCategoryFamily.VIOLENCE,
}


@dataclass(frozen=True)
class ShieldstralPolicy:
    """One yes/no question, the detection label it can emit, and its threshold setting."""

    id: str
    label: str
    query: str
    threshold_setting: str

    @property
    def family(self) -> McfCategoryFamily:
        return LABEL_FAMILIES[self.label]

    @property
    def threshold(self) -> float:
        return float(getattr(settings, self.threshold_setting))


SEXUAL_SCREEN = ShieldstralPolicy(
    id="sexual",
    label=SEXUAL_LABEL,
    query="Does this image show sexual activity or sexually suggestive content?",
    threshold_setting="shieldstral_sexual_threshold",
)
VIOLENCE_SCREEN = ShieldstralPolicy(
    id="violence",
    label=VIOLENCE_LABEL,
    query="Does this image contain violence or blood?",
    threshold_setting="shieldstral_violence_threshold",
)
SCREENS: tuple[ShieldstralPolicy, ...] = (SEXUAL_SCREEN, VIOLENCE_SCREEN)

# Asked only for sexual-positive frames; they choose families, never the confidence.
SEXUAL_ROUTES: tuple[ShieldstralPolicy, ...] = (
    ShieldstralPolicy(
        id="nudity",
        label=NUDITY_LABEL,
        query="Does this image contain nudity?",
        threshold_setting="shieldstral_nudity_route_threshold",
    ),
    ShieldstralPolicy(
        id="sex_act",
        label=SEXUAL_LABEL,
        query="Does this image show sexual intercourse or intimate sexual contact?",
        threshold_setting="shieldstral_sex_route_threshold",
    ),
)
ALL_QUERIES: tuple[str, ...] = tuple(
    dict.fromkeys(policy.query for policy in SCREENS + SEXUAL_ROUTES)
)


def policy_messages(query: str, *, instruct: str = INSTRUCT) -> list[dict]:
    """Build chat messages placing the frame as the Document after the policy query."""
    return [
        {"role": "system", "content": [{"type": "text", "text": SYSTEM_PROMPT}]},
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"<Instruct>: {instruct}\n\n<Query>: {query}\n\n<Document>: ",
                },
                {"type": "image"},
                {"type": "text", "text": "\n\n"},
            ],
        },
    ]


def positive_screens(
    scores: dict[str, float], screens: tuple[ShieldstralPolicy, ...] = SCREENS
) -> tuple[ShieldstralPolicy, ...]:
    """Return the screens whose P(yes) meets their configured threshold."""
    return tuple(screen for screen in screens if scores.get(screen.id, 0.0) >= screen.threshold)


def fired_route_labels(
    route_scores: dict[str, float], routes: tuple[ShieldstralPolicy, ...] = SEXUAL_ROUTES
) -> tuple[str, ...]:
    """Return the labels of every route at or above its threshold (possibly none)."""
    return tuple(
        dict.fromkeys(
            route.label for route in routes if route_scores.get(route.id, 0.0) >= route.threshold
        )
    )


def route_sexual_labels(
    route_scores: dict[str, float],
    routes: tuple[ShieldstralPolicy, ...] = SEXUAL_ROUTES,
    *,
    fallback: str = SEXUAL_LABEL,
) -> tuple[str, ...]:
    """Choose detection labels for a sexual-positive frame.

    Every route at or above its threshold contributes its label (nudity and sexual
    activity may both fire). When none fires, the family is unknown and ``fallback``
    is used: suggestive sexual content unless the fine pass resolved it from its run.
    """
    return fired_route_labels(route_scores, routes) or (fallback,)

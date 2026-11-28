"""Question schema, request building, and response parsing for `/v1/systemone` decision models."""

from __future__ import annotations

import base64
import io
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from scanner.modeling.model_core import (
    MCF_CATEGORY_FAMILY_LABELS,
    MCF_CATEGORY_REGISTRY,
)
from scanner.pipeline import decision_detection

# Reduced "fast" question set: the shipped detectors' families, with tokens only for
# nudity and sex.
FAST_FAMILIES = tuple(family.value for family in decision_detection.DETECTED_FAMILIES)
FAST_TOKEN_FAMILIES = ("nudity", "sex")

# Plain-language descriptions sent to the model; registry labels alone are too terse.
# The four detected families use the shipped decision detectors' wording.
FAMILY_SUBJECTS = {
    "commercial_content": "advertising, commercial content, or product placement",
    "discrimination": "discrimination or hateful treatment of a group of people",
    "dispensable_scenes": "a dispensable, pointless, or tedious scene",
    "fear": "frightening or disturbing content",
    "language": "offensive language or vulgar text",
    **{family.value: text for family, text in decision_detection.FAMILY_SUBJECTS.items()},
}

TOKEN_SUBJECTS = {
    "advertBreak": "an advert break or TV commercial",
    "consumerism": "consumerism or shopping",
    "productPlacement": "a prominently placed brand or product",
    "adultism": "adults demeaning children",
    "genderism": "gender-based discrimination",
    "idiocy": "idiotic or stupid behaviour",
    "tedious": "a tedious, uneventful scene",
    "antipsychotics": "antipsychotic medication",
    "depressants": "depressant drugs or sedatives",
    "hallucinogens": "hallucinogenic drugs",
    "stimulants": "stimulant drugs such as cocaine",
    "acrophobia": "dangerous heights",
    "arachnophobia": "spiders",
    "astraphobia": "thunder or lightning",
    "aviophobia": "a plane in danger or a plane crash",
    "chemophobia": "dangerous chemicals",
    "claustrophobia": "being trapped in a confined space",
    "coulrophobia": "clowns",
    "cynophobia": "aggressive dogs",
    "death": "death or a dead body",
    "dentophobia": "dentists or dental procedures",
    "emetophobia": "vomiting",
    "enochlophobia": "a dense crowd",
    "gerascophobia": "ageing or very old people",
    "grave": "graves or a cemetery",
    "hemophobia": "blood or gore",
    "hylophobia": "a dark, scary forest",
    "melissophobia": "bees or wasps",
    "misophonia": "unpleasant eating or chewing sounds",
    "musophobia": "mice or rats",
    "mysophobia": "dirt or germs",
    "nosocomephobia": "a hospital",
    "nyctophobia": "darkness at night",
    "siderodromophobia": "a train",
    "thalassophobia": "deep open water",
    "nameCalling": "name-calling or insults",
    "sexualDialogue": "sexual dialogue or text",
    "bareButtocks": "bare buttocks",
    "exposedGenitalia": "exposed genitalia",
    "fullNudity": "a fully nude person",
    "toplessness": "a topless person",
    "analSex": "anal sex",
    "coitus": "sexual intercourse",
    "objectification": "sexual objectification of a person",
    "oralSex": "oral sex",
    "premaritalSex": "premarital sex",
    "crueltyToAnimals": "cruelty to animals",
    "culturalViolence": "cultural or symbolic violence",
    "desecration": "desecration of a sacred place or object",
    "emotionalViolence": "threats, intimidation, or emotional abuse",
    "weapons": "weapons such as guns or knives being used or brandished",
}


@dataclass(frozen=True)
class Question:
    """One yes/no (`noul`) question; `key` is a family or `family/token` evaluation key."""

    question_id: str
    key: str
    text: str


# Single blood/gore question used by the `*-gore` ids; it maps only to `fear/hemophobia`.
GORE_QUESTIONS = (Question("q0", "fear/hemophobia", "Does this frame show blood or gore?"),)


def general_tokens() -> dict[str, str]:
    """Map each family to its general token (the one whose label ends with "(General)")."""

    result = {}
    for family, categories in MCF_CATEGORY_REGISTRY.items():
        for token, label in categories:
            if label.endswith("(General)"):
                result[family.value] = token
    return result


def build_questions(
    families: Iterable[str] | None = None, token_families: Iterable[str] | None = None
) -> list[Question]:
    """One question per family and per specific (non-general) token, in registry order.

    `families` limits the families asked (default: all); `token_families` limits which of
    them also get token questions (default: all asked families).
    """

    generals = general_tokens()
    wanted = None if families is None else set(families)
    with_tokens = None if token_families is None else set(token_families)
    questions: list[Question] = []
    for family, categories in MCF_CATEGORY_REGISTRY.items():
        name = family.value
        if wanted is not None and name not in wanted:
            continue
        questions.append(
            Question(
                f"q{len(questions)}",
                name,
                f"Does this video frame show {FAMILY_SUBJECTS[name]}?",
            )
        )
        if with_tokens is not None and name not in with_tokens:
            continue
        family_label = MCF_CATEGORY_FAMILY_LABELS[family].lower()
        for token, label in categories:
            if token == generals.get(name):
                continue
            subject = TOKEN_SUBJECTS.get(token, label.lower())
            questions.append(
                Question(
                    f"q{len(questions)}",
                    f"{name}/{token}",
                    f"Does this video frame show {subject} ({family_label})?",
                )
            )
    return questions


def family_questions(questions: list[Question]) -> list[Question]:
    """Stage-1 questions: one per family."""

    return [question for question in questions if "/" not in question.key]


def token_questions(
    questions: list[Question], family_scores: dict[str, float], threshold: float
) -> list[Question]:
    """Stage-2 questions: specific tokens of every family scoring at least `threshold`."""

    return [
        question
        for question in questions
        if "/" in question.key
        and family_scores.get(question.key.split("/", 1)[0], 0.0) >= threshold
    ]


def supported_keys(questions: list[Question]) -> list[str]:
    """Every key a decision backend emits, including `family/<general token>` aliases."""

    keys = [question.key for question in questions]
    families = {key for key in keys if "/" not in key}
    keys += [
        f"{family}/{token}" for family, token in general_tokens().items() if family in families
    ]
    return sorted(set(keys))


def image_data_url(path: Path, long_edge: int | None = None) -> str:
    """Encode an image file as a base64 data URL accepted by llama-server.

    With `long_edge`, larger images are downscaled in memory (JPEG q90); the file is
    never modified.
    """

    if long_edge:
        with Image.open(path) as image:
            if max(image.size) > long_edge:
                image = image.convert("RGB")
                image.thumbnail((long_edge, long_edge), Image.Resampling.LANCZOS)
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=90)
                encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
                return f"data:image/jpeg;base64,{encoded}"
    suffix = path.suffix.lower().lstrip(".")
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp", "gif": "gif"}.get(
        suffix, "jpeg"
    )
    return f"data:image/{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def build_request(image_url: str, questions: list[Question], state: str | None = None) -> dict:
    """Build one `/v1/systemone` body asking every question about a single image."""

    return {
        "state": state,
        "images": [image_url],
        "questions": {
            question.question_id: {"type": "noul", "instructions": question.text}
            for question in questions
        },
    }


def parse_response(body: dict, questions: list[Question]) -> dict[str, float]:
    """Map a `/v1/systemone` response to `{key: P(yes)}`; general tokens mirror their family.

    Raises ValueError when an answer is missing or not a probability.
    """

    answers = body.get("answers")
    if not isinstance(answers, dict):
        raise ValueError(f"response has no answers: {str(body)[:200]}")
    scores: dict[str, float] = {}
    for question in questions:
        answer = answers.get(question.question_id)
        value = answer.get("noul") if isinstance(answer, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"missing noul answer for {question.key}: {answer!r}")
        scores[question.key] = min(1.0, max(0.0, float(value)))
    for family, token in general_tokens().items():
        if family in scores:
            scores[f"{family}/{token}"] = scores[family]
    return scores

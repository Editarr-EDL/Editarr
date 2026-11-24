"""Record the inputs and validator outcome for language model calls made by a replay."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from scanner.models import FindingCreate

CLASSIFICATION_MAX_TOKENS = 512
DETECTION_MAX_TOKENS = 2048


@dataclass(frozen=True)
class TranscriptWord:
    text: str
    start: float
    end: float


class LanguageDecisionAttempt(BaseModel):
    """Keep one exact model request and the data needed to validate it again."""

    kind: Literal["detection", "classification"]
    batch_index: int = Field(ge=0)
    prompt: str
    response: str | None = None
    accepted: bool
    error: str | None = None
    max_tokens: int
    windows: list[list[TranscriptWord]] = Field(default_factory=list)
    classified_findings: dict[int, list[FindingCreate]] = Field(default_factory=dict)

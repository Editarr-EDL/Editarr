"""Chat turns, yes/no questions, and P(yes) scoring shared by the generative VLM backends.

The llama.cpp (`qwen_vlm`) and MLX (`qwen_mlx`) ids send identical turn strings, so their
results differ only by runtime.
"""

from __future__ import annotations

from scanner.pipeline import qwen_detector

from .schema import FAST_FAMILIES, GORE_QUESTIONS, Question, build_questions

TOP_LOGPROBS = qwen_detector.TOP_LOGPROBS
THRESHOLD = 0.5
TRANSCRIPT_LIMIT = 2000
USER = qwen_detector.USER
END = qwen_detector.END
# Generation prompt with thinking disabled, as the template renders enable_thinking=false.
ASSISTANT = qwen_detector.NON_THINKING_ASSISTANT
# Generation prompt for instruct models without a thinking mode (Qwen3-VL Instruct).
ASSISTANT_PLAIN = qwen_detector.ASSISTANT
SEQ_TURNS = (
    "Locate all sensitive objects with bounding boxes.",
    "List objects with their attributes and bboxes.",
    "Describe relationships as (subj, pred, obj) tuples.",
    "Describe any sensitive content visible in this image.",
)


def vlm_questions() -> list[Question]:
    """The four family questions (decision-model wording) plus the blood/gore question."""
    return [*build_questions(FAST_FAMILIES, ()), *GORE_QUESTIONS]


def yes_probability(top_logprobs: list[dict]) -> float:
    """P(yes) / (P(yes) + P(no)) over `{"token", "logprob"}` first-token candidates, scored
    exactly like the shipped Qwen3-VL detector; 0.0 when neither answer is among them."""
    return qwen_detector.yes_probability(
        [(str(entry.get("token", "")), float(entry["logprob"])) for entry in top_logprobs]
    )


def first_turn(marker: str, text: str, assistant: str = ASSISTANT) -> str:
    """Opening user turn: the frame (media marker) followed by `text`, then the reply prefix."""
    return f"{USER}{marker}{text}{END}{assistant}"


def next_turn(reply: str, text: str, assistant: str = ASSISTANT) -> str:
    """Close the previous reply and open the next user turn and reply prefix."""
    return f"{reply}{END}{USER}{text}{END}{assistant}"


def yes_no(question: Question) -> str:
    return f"{question.text} Answer yes or no."

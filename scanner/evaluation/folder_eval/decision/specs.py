"""GGUF decision models served by llama.cpp's `/v1/systemone` endpoint, and their variants.

Two-stage ids first ask one question per family, then the specific tokens of each family
scoring at least the stage-2 threshold (see `llama_backend`). The `*-fast` ids ask a
reduced set (drugs, nudity, sex, violence; tokens only for nudity and sex) about frames
downscaled to a 448 px long edge; the `*-f512` ids ask only those four family questions,
in one request, about 512 px frames, and `d1-3b-full4` asks them about full-size frames.
The `*-gore` ids ask one blood/gore question (`fear/hemophobia`) with their base's
settings, and `d1-3b-sev` adds the shared severity `score` questions to `d1-3b-full4`.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from .schema import FAST_FAMILIES, FAST_TOKEN_FAMILIES, GORE_QUESTIONS, Question

DEFAULT_FAST_LONG_EDGE = 448


@dataclass(frozen=True)
class DecisionModelSpec:
    """Static description of one GGUF decision model and how to serve and question it."""

    model_id: str
    display_name: str
    repo: str
    model_file: str
    mmproj_file: str
    server_args: tuple[str, ...]
    notes: str
    # Ask each flagged family's tokens in its own stage-2 request (keeps clef prompts small).
    stage2_per_family: bool = False
    # Text state sent with the image; some models (clef) reject a null state.
    state: str | None = None
    # Weights directory name under the models dir (defaults to model_id).
    weights_id: str | None = None
    # Families asked (None = all) and families that also get token questions (None = all).
    families: tuple[str, ...] | None = None
    token_families: tuple[str, ...] | None = None
    # Downscale frames to this long edge before sending (None = send the file as is).
    image_long_edge: int | None = None
    # False asks every question in one request per frame instead of two stages.
    two_stage: bool = True
    # Items scored in parallel by `run` when --concurrency is not given.
    default_concurrency: int = 1
    # Use the newest llama.cpp release unpacked in the models dir instead of PATH.
    bundled_server: bool = False
    # Fixed questions replacing the registry-derived set (families/token_families unused).
    questions: tuple[Question, ...] | None = None


# Server flags for the fast ids, tuned on an M4 Pro with llama.cpp b12556 (needed for
# lfm3-d1 packing: d1-omni reads up to 16 questions per pass). Within one request the
# image prefix is shared across up to `--parallel` question tasks; it is not reused
# across requests, so extra slots mainly serve concurrent items.
FAST_SERVER_ARGS = {
    "d1-omni-2b": ("-b", "8192", "-ub", "8192", "-c", "65536", "--parallel", "8"),
    "d1-3b": ("-b", "8192", "-ub", "8192", "-c", "65536", "--parallel", "32"),
    # clef reads a request's joint prompt in one ubatch (stage 2 is ~1.4k tokens).
    "clef-flash": ("-b", "4096", "-ub", "4096", "-c", "12288", "-fa", "on", "--parallel", "3"),
}

SPECS = {
    spec.model_id: spec
    for spec in (
        DecisionModelSpec(
            model_id="d1-omni-2b",
            display_name="LiquidAI d1-Omni-2B (Q8_0, llama.cpp)",
            repo="LiquidAI/d1-Omni-2B-GGUF",
            model_file="d1-Omni-2B-Q8_0.gguf",
            mmproj_file="mmproj-d1-Omni-2B-Q8_0.gguf",
            # lfm3-d1 evaluates one question per pass (no batching, image re-encoded each
            # time); extra slots only overlap image encoding.
            server_args=("-b", "8192", "-ub", "8192", "-c", "32768", "--parallel", "4"),
            notes="lfm3-d1-omni; llama.cpp evaluates each question as its own pass.",
        ),
        DecisionModelSpec(
            model_id="d1-3b",
            display_name="LiquidAI d1-3B (Q8_0, llama.cpp)",
            repo="LiquidAI/d1-3B-GGUF",
            model_file="d1-3B-Q8_0.gguf",
            mmproj_file="mmproj-d1-3B-Q8_0.gguf",
            # lfm2-d1 questions are independent sequences, so 16 slots batch them.
            server_args=("-b", "8192", "-ub", "8192", "-c", "32768", "--parallel", "16"),
            notes="lfm2-d1; each question is answered independently, batched over 16 slots.",
        ),
        DecisionModelSpec(
            model_id="clef-flash",
            display_name="Cloudflare Clef-Flash 9B (Q4_K_M, llama.cpp)",
            repo="ggml-org/Clef-Flash-GGUF",
            model_file="Clef-Flash-Q4_K_M.gguf",
            mmproj_file="mmproj-Clef-Flash-Q8_0.gguf",
            # A request's whole joint prompt must fit one ubatch: stage 1 is ~1.2k tokens
            # and the largest family (fear, 30 tokens) ~3k with the image.
            server_args=("-b", "4096", "-ub", "4096", "-c", "4096", "-fa", "on"),
            notes="clef decides all questions of a request jointly in one pass.",
            state="A still frame from a video.",
            stage2_per_family=True,
        ),
    )
}


def _fast(spec: DecisionModelSpec, **changes) -> DecisionModelSpec:
    """Reduced-question, downscaled-image variant of `spec` sharing its weights directory."""

    changes.setdefault("display_name", f"{spec.display_name}, fast set")
    return dataclasses.replace(
        spec,
        model_id=f"{spec.model_id}-fast",
        weights_id=spec.weights_id or spec.model_id,
        families=FAST_FAMILIES,
        token_families=FAST_TOKEN_FAMILIES,
        image_long_edge=DEFAULT_FAST_LONG_EDGE,
        bundled_server=True,
        **changes,
    )


FAST_SPECS = {
    spec.model_id: spec
    for spec in (
        _fast(
            SPECS["d1-omni-2b"], server_args=FAST_SERVER_ARGS["d1-omni-2b"], default_concurrency=2
        ),
        _fast(SPECS["d1-3b"], server_args=FAST_SERVER_ARGS["d1-3b"], default_concurrency=8),
        # Q8_0 prefills ~10 % faster than Q4_K_M on Metal and is closer to the BF16 model.
        _fast(
            SPECS["clef-flash"],
            display_name="Cloudflare Clef-Flash 9B (Q8_0, llama.cpp), fast set",
            model_file="Clef-Flash-Q8_0.gguf",
            server_args=FAST_SERVER_ARGS["clef-flash"],
            stage2_per_family=False,
            default_concurrency=2,
        ),
    )
}
SPECS.update(FAST_SPECS)


def _families_only(fast: DecisionModelSpec, base_id: str) -> DecisionModelSpec:
    """`<base>-f512`: the fast spec's runtime and flags, but only the four family questions
    in one request per frame (no token questions) on frames with a 512 px long edge."""

    return dataclasses.replace(
        fast,
        model_id=f"{base_id}-f512",
        display_name=fast.display_name.replace(", fast set", ", families only, 512 px"),
        token_families=(),
        two_stage=False,
        image_long_edge=512,
    )


F512_SPECS = {
    spec.model_id: spec
    for spec in (
        _families_only(FAST_SPECS[f"{base_id}-fast"], base_id)
        for base_id in ("d1-omni-2b", "d1-3b", "clef-flash")
    )
}
SPECS.update(F512_SPECS)

# `d1-3b-f512` without downscaling: frames are sent as cached (768 px long edge).
SPECS["d1-3b-full4"] = dataclasses.replace(
    SPECS["d1-3b-f512"],
    model_id="d1-3b-full4",
    display_name="LiquidAI d1-3B (Q8_0, llama.cpp), families only, full-size frames",
    image_long_edge=0,
)


def _gore(spec: DecisionModelSpec, base_id: str) -> DecisionModelSpec:
    """`<base>-gore`: `spec`'s image size, runtime and flags, asking only the single
    blood/gore question (key `fear/hemophobia`) per frame."""

    return dataclasses.replace(
        spec,
        model_id=f"{base_id}-gore",
        display_name=spec.display_name.split(", families only")[0] + ", blood/gore only",
        questions=GORE_QUESTIONS,
        families=None,
        token_families=None,
    )


GORE_SPECS = {
    spec.model_id: spec
    for spec in (
        _gore(SPECS["d1-3b-full4"], "d1-3b"),
        _gore(SPECS["d1-omni-2b-f512"], "d1-omni-2b"),
        _gore(SPECS["clef-flash-f512"], "clef-flash"),
    )
}
SPECS.update(GORE_SPECS)

SPECS["d1-3b-sev"] = dataclasses.replace(
    SPECS["d1-3b-full4"],
    model_id="d1-3b-sev",
    display_name="LiquidAI d1-3B (Q8_0, llama.cpp), families + severity, full-size frames",
)

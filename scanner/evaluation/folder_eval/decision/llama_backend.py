"""`LlamaServerDecisionBackend`: one GGUF decision model behind a private `llama-server`.

The backend lazily starts its server (see `llama_server`) and asks `noul` (P(yes))
questions per frame. Two-stage specs ask the specific tokens of each family scoring at
least the stage-2 threshold (0.25, override with `EDITARR_DM_STAGE2_THRESHOLD`); tokens
not asked are omitted and each frame lists its `asked` keys. `EDITARR_DM_IMAGE_LONG_EDGE`
overrides the downscale edge of specs that have one (0 disables).

Weights live under `<models dir>/<model>/` (see `llama_server.models_dir`).
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

from ..backends import BackendUnavailable
from . import llama_server
from .llama_server import LlamaServerProcess, runtime_label, server_binary, server_version
from .schema import (
    Question,
    build_questions,
    build_request,
    family_questions,
    image_data_url,
    parse_response,
    supported_keys,
    token_questions,
)
from .specs import DecisionModelSpec

DEFAULT_STAGE2_THRESHOLD = 0.25


def stage2_threshold() -> float:
    """Family probability at which a family's specific tokens are asked."""

    value = os.environ.get("EDITARR_DM_STAGE2_THRESHOLD")
    return float(value) if value else DEFAULT_STAGE2_THRESHOLD


def image_long_edge(spec: DecisionModelSpec) -> int | None:
    """The spec's downscale edge (None or 0 = send frames as-is).

    `EDITARR_DM_IMAGE_LONG_EDGE` overrides it for specs with a numeric edge (0 included);
    the original ids (None) always send frames as-is.
    """

    if spec.image_long_edge is None:
        return None
    value = os.environ.get("EDITARR_DM_IMAGE_LONG_EDGE")
    if value is None or value == "":
        return spec.image_long_edge or None
    return int(value) or None


class LlamaServerDecisionBackend:
    """Decision backend for one GGUF model served by a private `llama-server` process.

    `score_frames` is thread-safe, so `run --concurrency N` can score items in parallel.
    """

    def __init__(self, spec: DecisionModelSpec, models_dir: Path | None = None):
        self.spec = spec
        self.model_id = spec.model_id
        self.default_concurrency = spec.default_concurrency
        self.models_dir = models_dir or llama_server.models_dir()
        self.questions = (
            list(spec.questions)
            if spec.questions is not None
            else build_questions(spec.families, spec.token_families)
        )
        self._server = LlamaServerProcess(
            self.server_command, self.weights_dir / f"llama-server-{self.model_id}.log"
        )

    @property
    def weights_dir(self) -> Path:
        return self.models_dir / (self.spec.weights_id or self.model_id)

    def _binary(self) -> str | None:
        return server_binary(self.models_dir, bundled=self.spec.bundled_server)

    @functools.cached_property
    def runtime(self) -> str:
        """Runtime tag recorded on every result line, e.g. `llama.cpp b12556`."""

        return runtime_label(server_version(self._binary()))

    @property
    def peak_rss_bytes(self) -> int:
        return self._server.peak_rss_bytes

    def meta(self) -> dict:
        edge = image_long_edge(self.spec)
        binary = self._binary()
        staging = (
            f"Two-stage noul (P(yes)) questions: families, then the tokens of families with "
            f"P >= {stage2_threshold()}; unasked tokens are omitted (see per-frame `asked`)."
            if self.spec.two_stage
            else "All noul (P(yes)) questions in one request per frame."
        )
        return {
            "model_id": self.model_id,
            "display_name": self.spec.display_name,
            "supports": supported_keys(self.questions),
            "thresholds": {"*": 0.5},
            "stage2_threshold": stage2_threshold() if self.spec.two_stage else None,
            "image_long_edge": edge,
            "runtime": f"{self.runtime} llama-server /v1/systemone ({server_version(binary)})",
            "artifact": f"{self.spec.repo}: {self.spec.model_file} + {self.spec.mmproj_file}",
            "notes": (
                f"{self.spec.notes} {staging} General-token keys mirror their family."
                + (f" Frames downscaled to a {edge} px long edge." if edge else "")
            ),
        }

    def score_frames(self, paths: list[Path]) -> list[dict]:
        """Score each frame; `asked` lists every key with a score."""

        self._ensure_server()
        results = [self._score_frame(Path(path)) for path in paths]
        self._server.sample_rss()
        return results

    def _score_frame(self, path: Path) -> dict:
        url = image_data_url(path, image_long_edge(self.spec))
        if not self.spec.two_stage:
            scores = self._ask(url, self.questions)
        else:
            scores = self._ask(url, family_questions(self.questions))
            stage2 = token_questions(self.questions, scores, stage2_threshold())
            for group in self._stage2_groups(stage2):
                scores.update(self._ask(url, group))
        return {"scores": scores, "labels": [], "asked": sorted(scores)}

    def _ask(self, url: str, questions: list[Question]) -> dict[str, float]:
        return parse_response(self._post(build_request(url, questions, self.spec.state)), questions)

    def _stage2_groups(self, questions: list[Question]) -> list[list[Question]]:
        if not questions:
            return []
        if not self.spec.stage2_per_family:
            return [questions]
        groups: dict[str, list[Question]] = {}
        for question in questions:
            groups.setdefault(question.key.split("/", 1)[0], []).append(question)
        return list(groups.values())

    def close(self) -> None:
        self._server.close()

    def _ensure_server(self) -> None:
        self.check_available()
        self._server.ensure_started()

    def _post(self, body: dict) -> dict:
        try:
            return self._server.post("/v1/systemone", body)
        except RuntimeError as exc:
            raise RuntimeError(f"{self.model_id}: {exc}") from exc

    def server_command(self, port: int) -> list[str]:
        """Return the llama-server command line for this model on `port`."""

        return [
            self._binary() or "llama-server",
            "-m",
            str(self.weights_dir / self.spec.model_file),
            "--mmproj",
            str(self.weights_dir / self.spec.mmproj_file),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "-ngl",
            "999",
            "--no-webui",
            *self.spec.server_args,
        ]

    def check_available(self) -> None:
        """Raise BackendUnavailable unless the server binary and both weight files exist."""
        if self._binary() is None:
            raise BackendUnavailable(
                "unavailable: llama-server not found or not executable (install with "
                "`brew install llama.cpp`, unpack a release under the models dir, or set "
                "EDITARR_LLAMA_SERVER)"
            )
        missing = [
            name
            for name in (self.spec.model_file, self.spec.mmproj_file)
            if not (self.weights_dir / name).is_file()
        ]
        if missing:
            raise BackendUnavailable(
                f"unavailable: missing weights {missing} in {self.weights_dir}; download with "
                f"`uvx --from huggingface_hub hf download {self.spec.repo} "
                f"{self.spec.model_file} {self.spec.mmproj_file} --local-dir {self.weights_dir}`"
            )

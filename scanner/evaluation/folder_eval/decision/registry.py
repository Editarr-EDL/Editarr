"""Every decision and VLM model id, mapped to a backend factory (nothing loads on import)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import onejson_backends, qwen_mlx, qwen_vlm, scan_detectors
from .cascade import MODEL_ID as CASCADE_ID
from .cascade import CascadeBackend
from .d1_severity import D1SeverityBackend
from .llama_backend import LlamaServerDecisionBackend
from .specs import SPECS

# Llama.cpp decision ids whose backend adds questions beyond the spec's noul set.
_BACKEND_CLASSES: dict[str, type[LlamaServerDecisionBackend]] = {"d1-3b-sev": D1SeverityBackend}


def _llama(model_id: str) -> Callable[[], LlamaServerDecisionBackend]:
    backend_class = _BACKEND_CLASSES.get(model_id, LlamaServerDecisionBackend)
    return lambda: backend_class(SPECS[model_id])


BACKENDS: dict[str, Callable[[], Any]] = {
    **{model_id: _llama(model_id) for model_id in SPECS},
    **qwen_vlm.BACKENDS,
    **qwen_mlx.BACKENDS,
    **onejson_backends.BACKENDS,
    **scan_detectors.BACKENDS,
    CASCADE_ID: CascadeBackend,
}

"""Accelerated model execution with a CPU retry for device failures."""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

logger = logging.getLogger(__name__)
_T = TypeVar("_T")

CPU_PROVIDER = "CPUExecutionProvider"
COREML_PROVIDER = "CoreMLExecutionProvider"


class TorchInference:
    """Keep a worker's tensors on its preferred device, falling back to CPU once."""

    def __init__(self, torch: Any, device: Any, move: Callable[[Any], None]):
        self._torch = torch
        self._move = move
        self.device = device
        try:
            move(device)
        except RuntimeError as error:
            self._fallback(error)

    def _fallback(self, error: RuntimeError) -> None:
        if getattr(self.device, "type", self.device) != "mps":
            raise error
        logger.warning("PyTorch Metal failed. Retrying on CPU.", exc_info=True)
        self.device = self._torch.device("cpu")
        self._move(self.device)

    def run(self, predict: Callable[[Any], _T]) -> _T:
        """Run and synchronize one prediction before returning its result."""
        try:
            return predict(self.device)
        except RuntimeError as error:
            self._fallback(error)
        return predict(self.device)


class _CoreMLSession:
    """Expose the ONNX session operations used by the detectors."""

    def __init__(self, session: Any):
        self._session = session

    def get_providers(self):
        return self._session.get_providers()

    def run(self, output_names, input_feed):
        try:
            return self._session.run(output_names, input_feed)
        except Exception:
            if COREML_PROVIDER not in self.get_providers():
                raise
            logger.warning("ONNX Core ML inference failed. Retrying on CPU.", exc_info=True)
            self._session.set_providers([CPU_PROVIDER])
            return self._session.run(output_names, input_feed)


def create_coreml_session(
    ort: Any,
    path: Path,
    *,
    dimensions: dict[str, int],
    cache_dir: Path,
):
    """Compile an ONNX ML Program for the detector and fall back if Core ML fails."""
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        options = ort.SessionOptions()
        for name, size in dimensions.items():
            options.add_free_dimension_override_by_name(name, size)
        session = ort.InferenceSession(
            str(path),
            sess_options=options,
            providers=[
                (
                    COREML_PROVIDER,
                    {
                        "ModelFormat": "MLProgram",
                        "MLComputeUnits": "ALL",
                        "RequireStaticInputShapes": "0",
                        "EnableOnSubgraphs": "0",
                        "SpecializationStrategy": "FastPrediction",
                        "ModelCacheDirectory": str(cache_dir.resolve()),
                    },
                ),
                CPU_PROVIDER,
            ],
        )
    except Exception:
        logger.warning("ONNX Core ML initialization failed. Retrying on CPU.", exc_info=True)
        return ort.InferenceSession(str(path), providers=[CPU_PROVIDER])
    return _CoreMLSession(session)

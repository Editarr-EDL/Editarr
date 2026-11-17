"""Exercise the per-worker accelerator fallbacks without requiring Mac hardware."""

from types import SimpleNamespace

from scanner.pipeline.inference_devices import (
    COREML_PROVIDER,
    CPU_PROVIDER,
    TorchInference,
    create_coreml_session,
)


def test_metal_prediction_retries_once_and_keeps_model_on_cpu():
    devices = []
    execution = TorchInference(SimpleNamespace(device=str), "mps", devices.append)

    def predict(device):
        if device == "mps":
            raise NotImplementedError("missing Metal kernel")
        return device

    assert execution.run(predict) == "cpu"
    assert execution.run(predict) == "cpu"
    assert devices == ["mps", "cpu"]


def test_coreml_failure_retries_once_and_keeps_session_on_cpu(tmp_path):
    class Session:
        def __init__(self, _path, *, providers, **_kwargs):
            self.providers = [
                provider[0] if isinstance(provider, tuple) else provider for provider in providers
            ]
            self.calls = []

        def get_providers(self):
            return self.providers

        def set_providers(self, providers):
            self.providers = providers

        def run(self, _output_names, _input_feed):
            self.calls.append(self.providers[0])
            if COREML_PROVIDER in self.providers:
                raise RuntimeError("Core ML prediction failed")
            return self.calls.copy()

    ort = SimpleNamespace(SessionOptions=object, InferenceSession=Session)
    session = create_coreml_session(
        ort,
        tmp_path / "model.onnx",
        dimensions={},
        cache_dir=tmp_path / "coreml",
    )
    assert session.run(None, {}) == [COREML_PROVIDER, CPU_PROVIDER]
    assert session.run(None, {}) == [COREML_PROVIDER, CPU_PROVIDER, CPU_PROVIDER]

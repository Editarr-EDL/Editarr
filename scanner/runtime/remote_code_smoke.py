"""Prove a frozen runtime can load pinned ``trust_remote_code`` model code.

transformers copies remote modules into its modules cache and imports them from
there, so a frozen runtime needs that cache where Editarr configured it, writable,
and every module the pinned code imports. This smoke runs that same loader
on a generated module with the pinned imports, without weights or network access.
"""

from __future__ import annotations

import ast
import importlib
import os
import shutil
import sys
import tempfile
import uuid
from collections.abc import Iterable, Mapping
from pathlib import Path

_PROBE_MODULE = "editarr_remote_probe"
_PROBE_SIBLING = "editarr_remote_probe_sibling"


def remote_code_imports(sources: Iterable[Path]) -> dict[str, tuple[str, ...]]:
    """Return the third-party absolute imports of remote-code files.

    Maps each imported module to the sorted names imported from it (empty for a
    plain ``import``). Relative, standard-library and ``__future__`` imports are
    omitted. Imports inside functions are included because they run at inference.
    """
    found: dict[str, set[str]] = {}
    for source in sources:
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"), str(source))):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.setdefault(alias.name, set())
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.setdefault(node.module, set()).update(alias.name for alias in node.names)
    return {
        module: tuple(sorted(names))
        for module, names in sorted(found.items())
        if module.partition(".")[0] not in sys.stdlib_module_names
        and module != "__future__"
    }


def _probe_source(imports: Mapping[str, tuple[str, ...]]) -> str:
    lines = []
    for module, names in imports.items():
        if names:
            lines.append(f"from {module} import {', '.join(names)}")
        else:
            lines.append(f"import {module}")
    lines += [
        f"from .{_PROBE_SIBLING} import MARKER",
        "",
        "",
        "class RemoteCodeProbe:",
        "    marker = MARKER",
        "",
    ]
    return "\n".join(lines)


def _run_tiny_lfm2() -> None:
    """Run a randomly initialised two-layer LFM2 (conv and attention) forward pass."""
    import torch
    from transformers import Lfm2Config
    from transformers.models.lfm2.modeling_lfm2 import Lfm2ForCausalLM

    config = Lfm2Config(
        vocab_size=32,
        hidden_size=16,
        intermediate_size=32,
        num_hidden_layers=2,
        num_attention_heads=2,
        num_key_value_heads=1,
        max_position_embeddings=32,
        layer_types=["conv", "full_attention"],
    )
    with torch.no_grad():
        logits = Lfm2ForCausalLM(config).eval()(torch.tensor([[1, 2, 3, 4]])).logits
    if tuple(logits.shape) != (1, 4, 32) or not torch.isfinite(logits).all():
        raise RuntimeError("Packaged transformers LFM2 forward pass returned invalid logits")


def _modules_cache() -> Path:
    """Return transformers' modules cache after checking it honours the environment.

    huggingface_hub reads ``HF_HOME`` once at import, so importing it before Editarr
    configures the model cache would silently move remote code elsewhere.
    """
    from transformers import dynamic_module_utils

    configured = os.environ.get("HF_MODULES_CACHE") or (
        os.path.join(os.environ["HF_HOME"], "modules") if os.environ.get("HF_HOME") else None
    )
    actual = Path(dynamic_module_utils.HF_MODULES_CACHE).resolve()
    if configured is None or actual != Path(configured).resolve():
        raise RuntimeError(
            f"transformers modules cache {actual} ignores the configured model cache"
        )
    return actual


def smoke_remote_code(
    imports: Mapping[str, tuple[str, ...]], resolved_modules: Iterable[str]
) -> None:
    """Load a generated remote-code module through transformers' dynamic loader.

    ``imports`` are the pinned remote code's imports and ``resolved_modules`` the
    modules transformers resolves by name while loading that model. Also runs a tiny
    LFM2 forward pass. Raises RuntimeError naming the first missing piece.
    """
    from transformers import dynamic_module_utils

    modules_cache = _modules_cache()
    for module in resolved_modules:
        try:
            importlib.import_module(module)
        except ImportError as error:
            raise RuntimeError(f"Packaged runtime cannot import {module}: {error}") from error
    with tempfile.TemporaryDirectory(prefix="editarr-remote-code-") as temp:
        # transformers names the cached package after this directory; keep it an identifier.
        repository = Path(temp) / f"editarr_remote_probe_{uuid.uuid4().hex}"
        repository.mkdir()
        (repository / f"{_PROBE_MODULE}.py").write_text(_probe_source(imports), encoding="utf-8")
        (repository / f"{_PROBE_SIBLING}.py").write_text("MARKER = 'loaded'\n", encoding="utf-8")
        cached = modules_cache / "transformers_modules" / repository.name
        try:
            probe = dynamic_module_utils.get_class_from_dynamic_module(
                f"{_PROBE_MODULE}.RemoteCodeProbe", str(repository), local_files_only=True
            )
        except (ImportError, OSError) as error:
            raise RuntimeError(f"Packaged remote-code loading failed: {error}") from error
        finally:
            shutil.rmtree(cached, ignore_errors=True)
            for name in [name for name in sys.modules if repository.name in name]:
                del sys.modules[name]
    if probe.marker != "loaded":
        raise RuntimeError("Packaged remote-code loader did not resolve a relative import")
    _run_tiny_lfm2()

"""Optional runtime dependency imports for model-backed pipeline stages."""

from __future__ import annotations

import importlib
from types import ModuleType


def require_dependency(import_name: str, install_hint: str, requirer: str) -> ModuleType:
    """Import an optional dependency or raise an install hint.

    ``requirer`` starts the error message, for example
    ``"Image Safety Gate model requires"``.
    """
    try:
        return importlib.import_module(import_name)
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            f"{requirer} optional dependency "
            f"'{install_hint}'. Install the stack dependencies first."
        ) from exc

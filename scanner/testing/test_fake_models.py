"""Fail-closed authorization for the packaged fake-model fixture.

This module intentionally has no pipeline imports: queue admission must not load the
model stack merely to determine whether the separately packaged fixture is active.
"""

from __future__ import annotations

import os

from scanner.runtime.runtime_paths import get_runtime_paths, is_frozen_application

MARKER = "native-test-fake-models.marker"


def enabled() -> bool:
    """Allow fake adapters only in the marked frozen test artifact."""
    return (
        os.environ.get("EDITARR_TEST_FAKE_MODELS") == "1"
        and is_frozen_application()
        and (get_runtime_paths().resources_dir / MARKER).is_file()
    )

"""PIDs whose shutdown the installed verifier could not confirm.

The verifier runs as ``__main__`` while its recovery module imports it again as
``scripts.verify_installed_release``. Both copies record here, so ``main`` keeps the
scratch root while any owned process outlives cleanup.
"""

SURVIVING: list[int] = []

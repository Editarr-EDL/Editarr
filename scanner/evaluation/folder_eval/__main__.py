"""Entry point for `python -m scanner.evaluation.folder_eval`."""

import sys

from scanner.commands.folder_eval_cli import main

# Guarded so spawned frame-extraction workers do not re-run the CLI.
if __name__ == "__main__":
    sys.exit(main())

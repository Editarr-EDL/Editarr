#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
  echo "Error: uv is required. Install it from https://docs.astral.sh/uv/getting-started/installation/"
  exit 1
fi

case "${1:-dev}" in
  api|web|dev)
    mode="${1:-dev}"
    if [[ $# -gt 0 ]]; then
      shift
    fi
    exec uv run python scripts/run_development.py "$mode" "$@"
    ;;
  install)
    echo "Installing Python dependencies..."
    if ! python3 -c "import sys; exit(0 if sys.version_info >= (3, 11) else 1)" 2>/dev/null; then
      echo "Error: Python 3.11+ required. Found: $(python3 --version 2>&1)"
      exit 1
    fi
    uv sync --extra dev

    if ! command -v pnpm >/dev/null 2>&1; then
      if command -v corepack >/dev/null 2>&1; then
        echo "Enabling Corepack and activating pnpm..."
        corepack enable
        corepack prepare pnpm@latest --activate
      else
        echo "Error: pnpm not found and corepack is unavailable. Install pnpm or upgrade Node.js."
        exit 1
      fi
    fi

    echo "Installing web dependencies..."
    cd web && pnpm install
    echo "Done. Run './scripts/dev.sh dev' to start."
    ;;
  test-docker)
    echo "Building Docker test image..."
    docker build -f Dockerfile.test -t editarr-test .
    echo "Running pytest in Docker..."
    docker run --rm editarr-test
    ;;
  *)
    echo "Usage: $0 {install|dev|api|web|test-docker} [options]"
    exit 1
    ;;
esac

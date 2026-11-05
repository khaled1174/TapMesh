#!/usr/bin/env bash
set -euo pipefail
PRODUCT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_PYTHON="${TAPMESH_PYTHON:-$HOME/Library/Caches/TapMesh/venv/bin/python}"
cd "$PRODUCT_ROOT"
"$RUNTIME_PYTHON" scripts/check_contracts.py
"$RUNTIME_PYTHON" -m pytest -q
swift test --package-path "$PRODUCT_ROOT/macos" --scratch-path "$HOME/Library/Caches/TapMesh/swift-build" --jobs 2

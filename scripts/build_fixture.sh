#!/usr/bin/env bash
set -euo pipefail
PRODUCT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SIMULATOR_ID="${1:?usage: build_fixture.sh simulator-uuid}"
xcodebuild -project "$PRODUCT_ROOT/Fixtures/TapMeshFixture/TapMeshFixture.xcodeproj" \
  -scheme TapMeshFixture -destination "id=$SIMULATOR_ID" \
  -derivedDataPath "$HOME/Library/Caches/TapMesh/fixture-build" \
  -jobs 2 CODE_SIGNING_ALLOWED=NO ONLY_ACTIVE_ARCH=YES build

#!/usr/bin/env bash
# R9-5-6 T1: Build the rebuild execution sandbox image locally.
# Usage: bash build.sh [--push]
# Builds rebuild-execution-sandbox:local from the Dockerfile in this directory.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="rebuild-execution-sandbox:local"

echo "[sandbox] Building ${IMAGE_NAME} from ${SCRIPT_DIR}/Dockerfile ..."
docker build --pull -t "${IMAGE_NAME}" "${SCRIPT_DIR}"

echo "[sandbox] Build complete."
echo "[sandbox] Verify: docker images | grep rebuild-execution-sandbox"
docker images | grep rebuild-execution-sandbox || true

# Optional: via compose profile
# docker compose --profile sandbox build execution-sandbox

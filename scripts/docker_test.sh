#!/usr/bin/env bash
# docker_test.sh — builds the QUILLET Docker image and runs a one-shot smoke test.
#
# Usage:
#   ./scripts/docker_test.sh              # build + test
#   ./scripts/docker_test.sh --no-build   # skip rebuild (use existing image)
set -euo pipefail

cd "$(dirname "$0")/.."

IMAGE="quillet-test"
NO_BUILD=false

for arg in "$@"; do
  case $arg in
    --no-build) NO_BUILD=true ;;
    *) echo "Unknown argument: $arg"; exit 1 ;;
  esac
done

if [ "$NO_BUILD" = false ]; then
  echo "==> Building Docker image ($IMAGE)..."
  docker build -t "$IMAGE" .
  echo ""
fi

echo "==> Running Docker smoke test (one-shot container)..."
docker run --rm \
  -e QUILLET_MODE=web \
  -e QUILLET_ADMIN_PASSWORD=testpassword \
  -e QUILLET_ADMIN_UI=true \
  -e QUILLET_DB_BACKEND=sqlalchemy \
  -e QUILLET_DB_URL="sqlite:////tmp/quillet_smoke.db" \
  -e QUILLET_EMAIL_BACKEND=noop \
  -e QUILLET_BASE_URL="http://127.0.0.1:8000" \
  -e QUILLET_TEST_SLUG=test \
  "$IMAGE" \
  bash /app/scripts/docker_entrypoint_test.sh

echo ""
echo "Docker smoke test passed."

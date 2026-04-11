#!/usr/bin/env bash
# docker_entrypoint_test.sh — runs INSIDE the container.
# Bootstraps a test newsletter, starts gunicorn, runs HTTP tests, exits.
set -euo pipefail

PORT=8000
SLUG="${QUILLET_TEST_SLUG:-test}"

echo "==> Creating test newsletter (slug: $SLUG)..."
python - <<EOF
import os
from quillet.factory import _build_db
db = _build_db()
existing = db.get_newsletter("$SLUG")
if existing is None:
    db.create_newsletter(
        slug="$SLUG",
        name="Docker Smoke Test",
        from_email="test@example.com",
        from_name="Test",
        reply_to=None,
    )
    print("  Newsletter created.")
else:
    print("  Newsletter already exists, skipping.")
EOF

echo "==> Starting gunicorn on port $PORT..."
gunicorn \
  --bind "127.0.0.1:$PORT" \
  --workers 1 \
  --log-level warning \
  --daemon \
  --pid /tmp/quillet_test.pid \
  quillet._app:application

echo "==> Waiting for server to be ready..."
for i in $(seq 1 30); do
  if python -c "
import urllib.request, sys
try:
    urllib.request.urlopen('http://127.0.0.1:$PORT/$SLUG/')
    sys.exit(0)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
    echo "  Server ready (attempt $i)."
    break
  fi
  sleep 0.3
  if [ "$i" -eq 30 ]; then
    echo "  ERROR: server did not become ready in time."
    cat /tmp/gunicorn.log 2>/dev/null || true
    exit 1
  fi
done

echo "==> Running HTTP smoke tests..."
python /app/scripts/http_smoke_test.py
TEST_EXIT=$?

echo "==> Stopping gunicorn..."
if [ -f /tmp/quillet_test.pid ]; then
  kill "$(cat /tmp/quillet_test.pid)" 2>/dev/null || true
fi

exit $TEST_EXIT

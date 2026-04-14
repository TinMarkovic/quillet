#!/usr/bin/env bash
# Install the pre-commit hook. Run once after cloning: ./scripts/install-hooks.sh
set -e
HOOK=.git/hooks/pre-commit
cp scripts/pre-commit "$HOOK"
chmod +x "$HOOK"
echo "pre-commit hook installed."

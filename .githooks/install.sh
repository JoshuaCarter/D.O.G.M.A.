#!/usr/bin/env bash
# Point this repo at tracked hooks under .githooks/ (no copying into .git/hooks).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOKS_REL=".githooks"

git -C "$ROOT" config core.hooksPath "$HOOKS_REL"

for name in pre-commit pre-push sync-appdata.sh; do
	path="$ROOT/$HOOKS_REL/$name"
	if [[ ! -f "$path" ]]; then
		echo "missing hook: $path" >&2
		exit 1
	fi
	chmod +x "$path"
done

echo "core.hooksPath = $HOOKS_REL (local)"
echo "hooks: pre-commit (appdata + ALAO), pre-push (late appdata catch-up)"

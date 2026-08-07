#!/usr/bin/env bash
# Install DOGMA git hooks into .git/hooks/
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HOOKS_SRC="$ROOT/tools/git-hooks"
HOOKS_DST="$ROOT/.git/hooks"

for name in pre-commit pre-push; do
	src="$HOOKS_SRC/$name"
	dst="$HOOKS_DST/$name"
	if [[ ! -f "$src" ]]; then
		echo "missing hook: $src" >&2
		exit 1
	fi
	cp -f "$src" "$dst"
	chmod +x "$dst"
	echo "installed $dst"
done

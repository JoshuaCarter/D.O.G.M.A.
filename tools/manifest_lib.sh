# Shared manifest.yml loader for build.sh / package-fomod.sh.
# Usage: source this file, then dogma_load_manifest <min_level>
#   min_level 1|dev     → features with level >= dev
#   min_level 2|release → features with level >= release
# Sets FEATURES=(...) ; common is never listed (always included by callers).

dogma_load_manifest() {
	local min_level="${1:?min_level required (1/dev or 2/release)}"
	case "$min_level" in
		1 | dev | local) min_level=dev ;;
		2 | release) min_level=release ;;
		0 | off) min_level=off ;;
	esac
	local yml="${DOGMA_MANIFEST:-$ROOT/config/manifest.yml}"
	FEATURES=()
	if [[ ! -f "$yml" ]]; then
		echo "manifest: missing $yml" >&2
		return 1
	fi

	local py=()
	if command -v py >/dev/null 2>&1; then
		py=(py -3)
	elif command -v python3 >/dev/null 2>&1; then
		py=(python3)
	elif command -v python >/dev/null 2>&1; then
		py=(python)
	else
		echo "manifest: Python 3 required to read manifest.yml" >&2
		return 1
	fi

	# Do not use process substitution here — its exit status is easy to lose, and a
	# failed parse must abort the build (otherwise FEATURES=() + prune wipes the mod).
	local list_file rc
	list_file="$(mktemp)"
	set +e
	"${py[@]}" "$ROOT/tools/list_manifest_features.py" --manifest "$yml" --min-level "$min_level" >"$list_file"
	rc=$?
	set -e
	if (( rc != 0 )); then
		rm -f "$list_file"
		echo "manifest: failed to read $yml (exit $rc)" >&2
		return 1
	fi

	local rel
	while IFS= read -r rel || [[ -n "$rel" ]]; do
		rel="${rel//$'\r'/}"
		[[ -z "$rel" ]] && continue
		if [[ ! -d "$SRC/$rel" ]]; then
			rm -f "$list_file"
			echo "manifest: entry missing under src/: $rel" >&2
			return 1
		fi
		FEATURES+=("$rel")
	done < "$list_file"
	rm -f "$list_file"
	return 0
}

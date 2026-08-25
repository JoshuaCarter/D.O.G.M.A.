# Shared manifest catalog loader for build.sh / package-fomod.sh.
# Reads fomod/manifest.yml at repo root.
# Override with DOGMA_MANIFEST=<dir or manifest.yml>.
# Usage: source this file, then dogma_load_manifest <min_stage>
#   min_stage 1|local → path mods with stage >= local
#   min_stage beta    → path mods with stage >= beta (FOMOD)
#   min_stage gold    → gold only
# Sets FEATURES=(...) ; common is never listed (always included by callers).

dogma_load_manifest() {
	local min_stage="${1:?min_stage required (local|beta|gold)}"
	case "$min_stage" in
		1 | local | dev) min_stage=local ;;
		2 | release | gold) min_stage=gold ;;
		beta) min_stage=beta ;;
		0 | omit | off) min_stage=omit ;;
	esac
	local yml="${DOGMA_MANIFEST:-$ROOT/fomod/manifest.yml}"
	FEATURES=()
	if [[ -f "$yml" ]]; then
		:
	elif [[ -d "$yml" && -f "$yml/fomod/manifest.yml" ]]; then
		yml="$yml/fomod/manifest.yml"
	elif [[ -d "$yml" && -f "$yml/manifest.yml" ]]; then
		yml="$yml/manifest.yml"
	else
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
		echo "manifest: Python 3 required to read manifest catalog" >&2
		return 1
	fi

	local list_file rc
	list_file="$(mktemp)"
	set +e
	"${py[@]}" "$ROOT/dev/list_manifest_features.py" --manifest "$yml" --min-stage "$min_stage" --check-src >"$list_file" 2>"$list_file.err"
	rc=$?
	set -e
	if (( rc != 0 )); then
		if [[ -s "$list_file.err" ]]; then
			cat "$list_file.err" >&2
		else
			echo "manifest: failed to read $yml (exit $rc)" >&2
		fi
		rm -f "$list_file" "$list_file.err"
		return 1
	fi
	rm -f "$list_file.err"

	local rel
	while IFS= read -r rel || [[ -n "$rel" ]]; do
		rel="${rel//$'\r'/}"
		[[ -z "$rel" ]] && continue
		FEATURES+=("$rel")
	done <"$list_file"
	rm -f "$list_file"
	return 0
}

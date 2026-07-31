# Shared manifest catalog loader for build.sh / package-fomod.sh.
# Prefers config/ (manifest-third-party + dogma-features + dogma-tweaks), then legacy
# manifest.yml / features.yml. Override with DOGMA_MANIFEST=<path>.
# Usage: source this file, then dogma_load_manifest <min_stage>
#   min_stage 1|local|dev → path mods with stage >= dev
#   min_stage 2|release   → path mods with stage >= release
# Sets FEATURES=(...) ; common is never listed (always included by callers).

dogma_load_manifest() {
	local min_stage="${1:?min_stage required (1/local/dev or 2/release)}"
	case "$min_stage" in
		1 | local | dev) min_stage=dev ;;
		2 | release) min_stage=release ;;
		0 | omit | off) min_stage=omit ;;
	esac
	local yml="${DOGMA_MANIFEST:-}"
	if [[ -z "$yml" ]]; then
		if [[ -f "$ROOT/config/manifest-third-party.yml" && -f "$ROOT/config/manifest-dogma-features.yml" && -f "$ROOT/config/manifest-dogma-tweaks.yml" ]]; then
			yml="$ROOT/config"
		elif [[ -f "$ROOT/config/manifest-third-party.yml" && -f "$ROOT/config/manifest-dogma-mods.yml" && -f "$ROOT/config/manifest-dogma-tweaks.yml" ]]; then
			yml="$ROOT/config"
		elif [[ -f "$ROOT/config/manifest-remote.yml" && -f "$ROOT/config/manifest-dogma-features.yml" && -f "$ROOT/config/manifest-dogma-tweaks.yml" ]]; then
			yml="$ROOT/config"
		elif [[ -f "$ROOT/config/manifest.yml" ]]; then
			yml="$ROOT/config/manifest.yml"
		elif [[ -f "$ROOT/config/features.yml" ]]; then
			yml="$ROOT/config/features.yml"
		else
			yml="$ROOT/config"
		fi
	fi
	FEATURES=()
	if [[ ! -e "$yml" ]]; then
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

	# Do not use process substitution here - its exit status is easy to lose, and a
	# failed parse must abort the build (otherwise FEATURES=() + prune wipes the mod).
	local list_file rc
	list_file="$(mktemp)"
	set +e
	"${py[@]}" "$ROOT/tools/list_manifest_features.py" --manifest "$yml" --min-stage "$min_stage" >"$list_file"
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
	done <"$list_file"
	rm -f "$list_file"
	return 0
}

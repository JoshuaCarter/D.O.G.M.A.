# Shared manifest.ini loader for build.sh / package-fomod.sh.
# Usage: source this file, then dogma_load_manifest <min_level>
#   min_level 1 → paths with value >= 1 (local)
#   min_level 2 → paths with value >= 2 (release)
# Sets FEATURES=(...) ; common is never listed (always included by callers).

dogma_load_manifest() {
	local min_level="${1:?min_level required (1=local, 2=release)}"
	local ini="${DOGMA_MANIFEST:-$ROOT/config/manifest.ini}"
	FEATURES=()
	if [[ ! -f "$ini" ]]; then
		echo "manifest: missing $ini" >&2
		return 1
	fi
	local line rel val
	while IFS= read -r line || [[ -n "$line" ]]; do
		line="${line//$'\r'/}"
		line="${line#"${line%%[![:space:]]*}"}"
		line="${line%"${line##*[![:space:]]}"}"
		[[ -z "$line" ]] && continue
		[[ "$line" == \;* || "$line" == \#* ]] && continue
		[[ "$line" == \[* ]] && continue
		if [[ "$line" == *=* ]]; then
			rel="${line%%=*}"
			val="${line#*=}"
		else
			# Bare path = release (legacy)
			rel="$line"
			val=2
		fi
		rel="${rel%"${rel##*[![:space:]]}"}"
		rel="${rel#"${rel%%[![:space:]]*}"}"
		val="${val%"${val##*[![:space:]]}"}"
		val="${val#"${val%%[![:space:]]*}"}"
		[[ -z "$rel" || "$rel" == "common" ]] && continue
		case "$val" in
			0 | 1 | 2) ;;
			*)
				echo "manifest: bad value for $rel (want 0|1|2, got '$val')" >&2
				return 1
				;;
		esac
		[[ "$val" -ge "$min_level" ]] || continue
		if [[ ! -d "$SRC/$rel" ]]; then
			echo "manifest: entry missing under src/: $rel" >&2
			return 1
		fi
		FEATURES+=("$rel")
	done < "$ini"
	return 0
}

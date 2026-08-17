#!/usr/bin/env bash
# Compile src/ into gamedata for DOGMA.
#
# Layout (MCM-aligned):
#   src/_common/<gamedata-rel>/...          -> <out>/<gamedata-rel>/...  (names kept)
#   src/_common/<feat>/<gamedata-rel>/...   -> always-on nested (path_key=<feat>)
#   src/<category>/<feature>/<gamedata-rel>/... -> <out>/<gamedata-rel>/...  (merged)
#   src/_debug/<gamedata-rel>/...           -> top-level feature; path_key = debug
#   src/<feature>/<gamedata-rel>/...        -> same (top-level; path_key = feature)
#
#   Reserved dirs _common / _debug use a leading underscore on disk only.
#   Manifest paths, path_keys, and MCM ids stay unprefixed (common, debug).
#
#   src/.../assets/...                      authoring only (ignored; not shipped)
#   src/.../installer/...                   leftover skip (FOMOD images live in fomod/images/)
#   *.pdn                                   authoring only (Paint.NET; never shipped)
#   *.png under gamedata                    authoring only (convert to DDS; MO2 modroot PNGs still ship)
#   src/<category>/<feature>/db/...         EXCEPTION: files under <mod>/db/
#                                           (Anomaly archive mods, e.g. db/mods/*.db0)
#   src/<category>/<feature>/*.py           EXCEPTION: <mod>/<file> (run-from-mod-dir tools)
#   src/_common/*.py                        EXCEPTION: <mod>/<file>
#   src/<category>/<feature>/modlist_delta.txt → gamedata/configs/dogma/modlist_deltas/<path_key>.txt
#
# Scripts (prefix applied at build - src keeps short names like main.script):
#   _common/scripts/__dogma_*        -> private impls
#   _common/scripts/dogma_mcm.script -> MCM-gather API (dogma_mcm.attach / …)
#   _common/scripts/dogma.script     -> game-time API (dogma.mcm / .dbg / .load)
#                               game-time: dogma.*; MCM scripts: dogma_mcm.*
#                               bare _* names other than __dogma_* skipped
#   …/scripts/mcm.script      -> dogma_{path}_mcm.script   (*mcm.script glob)
#                               feature _G.dogma_*_conf lives in this file
#   …/scripts/modxml_*.script -> modxml_dogma_{path}_*.script
#                               keep modxml_ prefix - Modded Exes only gathers
#                               that glob for DXML on_xml_read injection
#   …/scripts/override/*.script -> scripts/<basename>.script  (exact name - only
#                               when disabled.ini cannot cover the conflict:
#                               exo MCM replace, blank vanilla game_fast_travel,
#                               Blindside/Keybinds scripts we must not disable)
#   …/scripts/**/*.script     -> scripts/zzzz_dogma_{path}_<stem>.script
#
# Env:
#   DOGMA_ONLY=spec    what to build:
#                        (empty|all)  → common + features with manifest stage >= dev
#                        common       → common only
#                        cat/feat     → that feature only (e.g. game/free_zoom; ignores manifest)
#                        feat         → top-level feature only (e.g. debug)
#   DOGMA_DEPLOY=path  after a fresh build/, full-replace this MO2 mod folder
#                      (gamedata + meta). Full builds only (not DOGMA_ONLY).
#   DOGMA_OUT=path     override output gamedata (default: build/gamedata). Set by
#                      package-fomod; skips wiping build/ and skips DOGMA_DEPLOY.
#   DOGMA_NO_ALAO=1    skip ALAO on src/ before staging
#
# Default (no DOGMA_OUT): wipe build/, stage → build/, then optional full-replace
# deploy. package-fomod sets DOGMA_OUT and merges into its own stage dirs.
set -eEuo pipefail

build_fail() {
	trap - ERR
	local msg="${1:-build failed}"
	local code="${2:-1}"
	printf '\033[31m%s\033[0m\n' "build: FAILED — $msg" >&2
	exit "$code"
}

build_on_exit() {
	local rc=$?
	rm -rf "${STAGE:-}" "${STAGE_MODROOT:-}" 2>/dev/null || true
	rm -f "${MANIFEST:-}" "${MANIFEST_MODROOT:-}" 2>/dev/null || true
	exit "$rc"
}

trap 'build_fail "unexpected error (line $LINENO)"' ERR
trap 'build_on_exit' EXIT

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/src"
ONLY="${DOGMA_ONLY:-all}"
BUILD_ROOT="$ROOT/build"

# shellcheck source=manifest_lib.sh
source "$ROOT/tools/manifest_lib.sh"

# DOGMA_OUT = packaging / override. Otherwise always build into build/.
DEPLOY_MOD=""
FRESH_BUILD=0
if [[ -n "${DOGMA_OUT:-}" ]]; then
	OUT="$DOGMA_OUT"
	MODROOT_OUT="$(dirname "$OUT")"
	mkdir -p "$MODROOT_OUT"
	MODROOT_OUT="$(cd "$MODROOT_OUT" && pwd)"
else
	OUT="$BUILD_ROOT/gamedata"
	MODROOT_OUT="$BUILD_ROOT"
	FRESH_BUILD=1
	if [[ -n "${DOGMA_DEPLOY:-}" ]]; then
		DEPLOY_MOD="${DOGMA_DEPLOY%/}"
	fi
fi

GAMEDATA_ROOTS="scripts configs textures meshes anims sounds spawns materials"
# Feature buckets that ship next to gamedata/ (MO2 mod root), not into gamedata.
MODROOT_BUCKET="mo2"
# Space-separated extra mod-root buckets (same mapping rules as mo2/).
MODROOT_BUCKETS="mo2 db"

STAGE="$(mktemp -d)"
STAGE_MODROOT="$(mktemp -d)"
MANIFEST="$(mktemp)"
MANIFEST_MODROOT="$(mktemp)"

if [[ ! -d "$SRC" ]]; then
	build_fail "missing $SRC"
fi

# Same ALAO command as DOGMA Optimize, on local src/ only (--direct).
# alao_local.py clears *.alao-bak first so src/ is always re-fixed.
run_alao_local() {
	if [[ -n "${DOGMA_NO_ALAO:-}" ]]; then
		echo "build: skipping ALAO (DOGMA_NO_ALAO set)"
		return 0
	fi
	local py=()
	if command -v py >/dev/null 2>&1; then
		py=(py -3)
	elif command -v python3 >/dev/null 2>&1; then
		py=(python3)
	elif command -v python >/dev/null 2>&1; then
		py=(python)
	else
		echo "build: Python 3 required for ALAO (or set DOGMA_NO_ALAO=1)" >&2
		return 1
	fi
	echo "build: ALAO on src/…"
	"${py[@]}" "$ROOT/tools/alao_local.py"
}

should_skip_name() {
	local base="$1"
	case "$base" in
		README | README.* | MOVE_MAP | MOVE_MAP.* | .gitkeep | .DS_Store | Thumbs.db) return 0 ;;
		assets | installer | __pycache__) return 0 ;;
		*.alao-bak | *.pyc | *.pyo | *.meta) return 0 ;;
		# Authoring / pack source only — shipped via db/mods/*.db0 instead.
		*.aimap | *.pdn) return 0 ;;
		# __dogma_*: private; dogma_mcm: common index.
		dogma_mcm.script | dogma.script | __dogma_*.script | _common | _debug) return 1 ;;
		_*) return 0 ;;
		*) return 1 ;;
	esac
}

is_modroot_bucket() {
	local name="$1"
	local b
	for b in $MODROOT_BUCKETS; do
		[[ "$name" == "$b" ]] && return 0
	done
	return 1
}

is_gamedata_root() {
	local name="$1"
	local r
	for r in $GAMEDATA_ROOTS; do
		[[ "$name" == "$r" ]] && return 0
	done
	return 1
}

# Manifest / DOGMA_ONLY path → folder under src/ (_common / _debug on disk only).
src_feature_dir() {
	case "$1" in
		common) echo "_common" ;;
		debug) echo "_debug" ;;
		*) echo "$1" ;;
	esac
}

# Folder under src/ → logical feature path (inverse of src_feature_dir for top-level).
src_dir_to_feature() {
	case "$1" in
		_common) echo "common" ;;
		_debug) echo "debug" ;;
		*) echo "$1" ;;
	esac
}

# path_key: category_feature or top-level feature. Empty = keep basename (_common/).
script_dest_basename() {
	local path_key="$1"
	local src_base="$2"
	if [[ -z "$path_key" ]]; then
		echo "$src_base"
		return 0
	fi
	local stem="${src_base%.script}"
	# Authoring may keep a legacy zzzz_ prefix; never double-prefix.
	stem="${stem#zzzz_}"
	case "$stem" in
		mcm)
			echo "dogma_${path_key}_mcm.script"
			return 0
			;;
		modxml_*)
			echo "modxml_dogma_${path_key}_${stem#modxml_}.script"
			return 0
			;;
	esac
	echo "zzzz_dogma_${path_key}_${stem}.script"
}

# Map one src file. Sets: _emit_kind (gamedata|modroot) _emit_src _emit_rel _emit_path_key _emit_base
map_src_file() {
	local src_path="$1"
	local rel="${src_path#"$SRC"/}"
	rel="${rel//\\/\/}"
	local base="${src_path##*/}"

	should_skip_name "$base" && return 1

	local part
	IFS=/ read -r -a parts <<< "$rel"
	for part in "${parts[@]}"; do
		case "$part" in
			assets | installer) return 1 ;;
			_common | _debug) ;; # reserved shippable src roots
			_*)
				[[ "$part" == "$base" ]] || return 1
				;;
		esac
	done

	local path_key="" bucket_rel=""

	if [[ "$rel" == _common/* ]]; then
		bucket_rel="${rel#_common/}"
		path_key=""
		# EXCEPTION: src/_common/*.py → <mod>/<file>
		if [[ "$bucket_rel" == *.py && "$bucket_rel" != */* ]]; then
			_emit_kind="modroot"
			_emit_src="$src_path"
			_emit_rel="$base"
			_emit_path_key=""
			_emit_base="$base"
			return 0
		fi
		# EXCEPTION: _common/mo2|db/ → <MO2 mod>/<bucket>/ (always-on core tools / archives).
		local common_bucket="${bucket_rel%%/*}"
		if is_modroot_bucket "$common_bucket"; then
			bucket_rel="${bucket_rel#"$common_bucket"/}"
			[[ -n "$bucket_rel" && "$bucket_rel" != "$common_bucket" ]] || return 1
			_emit_kind="modroot"
			_emit_src="$src_path"
			_emit_rel="$common_bucket/$bucket_rel"
			_emit_path_key=""
			_emit_base="$base"
			return 0
		elif ! is_gamedata_root "$common_bucket"; then
			# Nested always-on: _common/<feat>/<gamedata-root>/... → path_key=<feat>
			local rest="${bucket_rel#"$common_bucket"/}"
			local nested_bucket="${rest%%/*}"
			if [[ -n "$rest" && "$rest" != "$bucket_rel" ]] && is_gamedata_root "$nested_bucket"; then
				path_key="$common_bucket"
				bucket_rel="$rest"
			fi
		fi
	else
		local cat="${parts[0]}"
		if is_gamedata_root "$cat"; then
			bucket_rel="$rel"
			path_key=""
		elif (( ${#parts[@]} == 3 )) && [[ "$base" == *.py ]]; then
			# src/<cat>/<feat>/*.py → <mod>/<file>
			local feat="${parts[1]}"
			should_skip_name "$feat" && return 1
			_emit_kind="modroot"
			_emit_src="$src_path"
			_emit_rel="$base"
			_emit_path_key="${cat}_${feat}"
			_emit_base="$base"
			return 0
		elif (( ${#parts[@]} == 3 )) && [[ "$base" == "modlist_delta.txt" ]]; then
			# src/<cat>/<feat>/modlist_delta.txt → gamedata/configs/dogma/modlist_deltas/<path_key>.txt
			local feat="${parts[1]}"
			should_skip_name "$feat" && return 1
			_emit_kind="gamedata"
			_emit_src="$src_path"
			_emit_rel="configs/dogma/modlist_deltas/${cat}_${feat}.txt"
			_emit_path_key="${cat}_${feat}"
			_emit_base="$base"
			return 0
		elif (( ${#parts[@]} >= 3 )) && { is_gamedata_root "${parts[1]}" || is_modroot_bucket "${parts[1]}"; }; then
			# Top-level feature: src/<feat|/ _debug>/<gamedata-root|mo2|db>/...
			local feat_dir="$cat"
			local feat
			feat="$(src_dir_to_feature "$feat_dir")"
			local bucket="${parts[1]}"
			should_skip_name "$feat_dir" && return 1
			path_key="$feat"

			if is_modroot_bucket "$bucket"; then
				bucket_rel="${rel#"$feat_dir/$bucket/"}"
				[[ -n "$bucket_rel" ]] || return 1
				_emit_kind="modroot"
				_emit_src="$src_path"
				_emit_rel="$bucket/$bucket_rel"
				_emit_path_key="$path_key"
				_emit_base="$base"
				return 0
			fi

			is_gamedata_root "$bucket" || return 1
			bucket_rel="${rel#"$feat_dir/"}"
		else
			(( ${#parts[@]} >= 4 )) || return 1
			local feat="${parts[1]}"
			local bucket="${parts[2]}"
			should_skip_name "$feat" && return 1
			path_key="${cat}_${feat}"

			# EXCEPTION: mo2|db/ → <MO2 mod>/<bucket>/ (sibling of gamedata/).
			if is_modroot_bucket "$bucket"; then
				bucket_rel="${rel#"$cat/$feat/$bucket/"}"
				[[ -n "$bucket_rel" ]] || return 1
				_emit_kind="modroot"
				_emit_src="$src_path"
				_emit_rel="$bucket/$bucket_rel"
				_emit_path_key="$path_key"
				_emit_base="$base"
				return 0
			fi

			is_gamedata_root "$bucket" || return 1
			bucket_rel="${rel#"$cat/$feat/"}"
		fi
	fi

	local bucket="${bucket_rel%%/*}"
	is_gamedata_root "$bucket" || return 1

	local dest_rel="$bucket_rel"
	if [[ -n "$path_key" && "$base" == *.script && "$bucket" == "scripts" ]]; then
		# Flat under scripts/. override/ keeps exact basename (replace rival file).
		if [[ "$bucket_rel" == scripts/override/* ]]; then
			dest_rel="scripts/$base"
		else
			dest_rel="scripts/$(script_dest_basename "$path_key" "$base")"
		fi
	fi

	_emit_kind="gamedata"
	_emit_src="$src_path"
	_emit_rel="$dest_rel"
	_emit_path_key="$path_key"
	_emit_base="$base"
	return 0
}

# Write into STAGE (correct layout). Manifest records relative path for prune.
stage_file() {
	local kind="$1"
	local src_path="$2"
	local dest_rel="$3"
	local path_key="$4"
	local base="$5"
	local staged

	if [[ "$kind" == "modroot" ]]; then
		staged="$STAGE_MODROOT/$dest_rel"
		mkdir -p "${staged%/*}"
		cp "$src_path" "$staged"
		printf '%s\n' "$dest_rel" >> "$MANIFEST_MODROOT"
		return 0
	fi

	staged="$STAGE/$dest_rel"
	mkdir -p "${staged%/*}"

	cp "$src_path" "$staged"
	printf '%s\n' "$dest_rel" >> "$MANIFEST"
}

# Precomputed once (src_in_scope is hot on Windows — avoid per-file $(subshell)s).
FEATURE_SRC_DIRS=()
ONLY_SRC_DIR=""

src_in_scope() {
	local rel="${1#"$SRC"/}"
	rel="${rel//\\/\/}"
	local sdir
	case "$ONLY" in
		all | "")
			# common + debug are stage:OMIT but still ship on a full local build.
			[[ "$rel" == _common/* || "$rel" == _debug/* ]] && return 0
			for sdir in "${FEATURE_SRC_DIRS[@]}"; do
				[[ "$rel" == "$sdir"/* || "$rel" == "$sdir" ]] && return 0
			done
			return 1
			;;
		common)
			[[ "$rel" == _common/* ]]
			;;
		debug)
			[[ "$rel" == _debug/* ]]
			;;
		*)
			[[ "$rel" == "$ONLY_SRC_DIR"/* || "$rel" == "$ONLY_SRC_DIR" ]]
			;;
	esac
}

case "$ONLY" in
	all | "" | common | debug) ;;
	*)
		ONLY_SRC_DIR="$(src_feature_dir "$ONLY")"
		[[ -d "$SRC/$ONLY_SRC_DIR" ]] || build_fail "DOGMA_ONLY=$ONLY not found at $SRC/$ONLY_SRC_DIR"
		;;
esac

if [[ -n "$DEPLOY_MOD" && "$ONLY" != "all" && "$ONLY" != "" ]]; then
	build_fail "DOGMA_DEPLOY requires full build (DOGMA_ONLY=$ONLY would replace the whole mod)"
fi

if [[ "$ONLY" == "all" || "$ONLY" == "" ]]; then
	dogma_load_manifest 1 || build_fail "manifest load failed"
	if ((${#FEATURES[@]} == 0)) && [[ -z "${DOGMA_ALLOW_EMPTY:-}" ]]; then
		build_fail "manifest yielded 0 features (fix YAML or set DOGMA_ALLOW_EMPTY=1)"
	fi
	echo "build: config manifests stage>=dev (${#FEATURES[@]} features)"
	FEATURE_SRC_DIRS=()
	for f in "${FEATURES[@]}"; do
		FEATURE_SRC_DIRS+=("$(src_feature_dir "$f")")
	done
fi

# Fresh build/: clear everything first so ALAO report + outputs land in an empty tree.
wipe_dir_contents() {
	local root="$1"
	mkdir -p "$root"
	find "$root" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
}

if (( FRESH_BUILD )); then
	echo "build: wiping $BUILD_ROOT"
	wipe_dir_contents "$BUILD_ROOT"
fi

echo "build: out=$OUT"
echo "build: modroot=$MODROOT_OUT"
if [[ -n "$DEPLOY_MOD" ]]; then
	echo "build: deploy=$DEPLOY_MOD (full replace after build)"
fi

run_alao_local || build_fail "ALAO failed"
echo "building..."

# Stage every shippable file (quiet).
# Prune authoring trees early - assets/ can hold multi-GB .blend/.mp4 that must
# never ship (map_src_file also rejects them; skipping the walk avoids Windows
# AV + bash path work on those files).
_emit_kind=""
while IFS= read -r -d '' src_path; do
	src_in_scope "$src_path" || continue
	map_src_file "$src_path" || continue
	case "$_emit_base" in
		*.png) [[ "$_emit_kind" == "gamedata" ]] && continue ;;
	esac
	stage_file "$_emit_kind" "$_emit_src" "$_emit_rel" "$_emit_path_key" "$_emit_base"
done < <(find "$SRC" \( -name assets -o -name installer -o -name __pycache__ \) -prune -o -type f -print0)

sort -u "$MANIFEST" -o "$MANIFEST"
sort -u "$MANIFEST_MODROOT" -o "$MANIFEST_MODROOT"

count="$(wc -l < "$MANIFEST" | tr -d ' ')"
count_modroot="$(wc -l < "$MANIFEST_MODROOT" | tr -d ' ')"

# Fresh build already wiped; packaging paths just ensure dirs exist.
mkdir -p "$OUT"
mkdir -p "$MODROOT_OUT"
cp -a "$STAGE"/. "$OUT"/
if [[ "$count_modroot" != "0" ]]; then
	cp -a "$STAGE_MODROOT"/. "$MODROOT_OUT"/
fi

write_mod_meta() {
	local dest="$1"
	cp "$ROOT/meta.ini" "$dest/meta.ini"
	if [[ -f "$ROOT/.mod_id" ]]; then
		cp "$ROOT/.mod_id" "$dest/.mod_id"
	else
		rm -f "$dest/.mod_id"
	fi
	if [[ -f "$ROOT/INFO.md" ]]; then
		cp "$ROOT/INFO.md" "$dest/INFO.md"
	else
		rm -f "$dest/INFO.md"
	fi
}

# Local build/ is a complete mod tree (meta included).
if (( FRESH_BUILD )); then
	write_mod_meta "$MODROOT_OUT"
fi

# Full-replace MO2 mod from build/ (mod files only — not alao_report.html).
if [[ -n "$DEPLOY_MOD" ]]; then
	echo "build: replacing $DEPLOY_MOD"
	wipe_dir_contents "$DEPLOY_MOD"
	cp -a "$BUILD_ROOT/gamedata" "$DEPLOY_MOD/gamedata"
	if [[ -d "$BUILD_ROOT/mo2" ]]; then
		cp -a "$BUILD_ROOT/mo2" "$DEPLOY_MOD/mo2"
	fi
	if [[ -d "$BUILD_ROOT/db" ]]; then
		cp -a "$BUILD_ROOT/db" "$DEPLOY_MOD/db"
	fi
	# Loose files at mod root (e.g. dogma_sfx_prefetch.py).
	if [[ -f "$MANIFEST_MODROOT" ]]; then
		while IFS= read -r rel; do
			[[ -z "$rel" || "$rel" == */* ]] && continue
			if [[ -f "$BUILD_ROOT/$rel" ]]; then
				cp -a "$BUILD_ROOT/$rel" "$DEPLOY_MOD/$rel"
			fi
		done < "$MANIFEST_MODROOT"
	fi
	write_mod_meta "$DEPLOY_MOD"
fi

printf '\033[32m%s\033[0m\n' "build: done ($count gamedata, $count_modroot modroot) at $(date '+%Y-%m-%d %H:%M:%S')"

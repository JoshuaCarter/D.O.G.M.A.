#!/usr/bin/env bash
# Compile src/ into gamedata for DOGMA.
#
# Layout (MCM-aligned):
#   src/_common/<gamedata-rel>/...          -> <out>/<gamedata-rel>/...  (names kept)
#   src/<category>/<feature>/<gamedata-rel>/... -> <out>/<gamedata-rel>/...  (merged)
#   src/_debug/<gamedata-rel>/...           -> top-level feature; path_key = debug
#   src/<feature>/<gamedata-rel>/...        -> same (top-level; path_key = feature)
#
#   Reserved dirs _common / _debug use a leading underscore on disk only.
#   Manifest paths, path_keys, and MCM ids stay unprefixed (common, debug).
#
#   src/.../assets/...                      authoring only (ignored; not shipped)
#   src/.../installer/image.png             optional FOMOD hover image (not shipped into gamedata)
#   src/_common/mo2/...                     EXCEPTION: files under <mod>/mo2/
#   src/<category>/<feature>/mo2/...        (sibling of gamedata/), e.g. mo2/tools/…
#   src/<category>/<feature>/db/...         EXCEPTION: files under <mod>/db/
#                                           (Anomaly archive mods, e.g. db/mods/*.db0)
#
# Scripts (prefix applied at build - src keeps short names like main.script):
#   _common/scripts/*         -> same basename (dogma_common, dogma_mcm,
#                               dogma_key_mirror_mcm, _dogma_input, dogma_xlibs, …)
#                               no zzzz_ - always before every feature script
#                               *mcm.script also participates in MCM gather
#                               (key mirrors live in dogma_key_mirror_mcm)
#                               _dogma_input kept (underscore sorts early);
#                               bare _* names are otherwise skipped
#   …/scripts/_conf.script    -> dogma_{path}_conf.script
#                               no zzzz_ - before that feature's zzzz_ body scripts
#   …/scripts/mcm.script      -> dogma_{path}_mcm.script   (*mcm.script glob)
#                               _conf is prepended so main-menu MCM (which only
#                               loads *mcm.script) still gets MOD_ID + defaults
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
#                        (empty|all)  → common + features with config/features.yml >= local
#                        common       → common only
#                        cat/feat     → that feature only (e.g. gameplay/free_zoom; ignores manifest)
#                        feat         → top-level feature only (e.g. debug)
#   DOGMA_DEPLOY=path  after a fresh build/, full-replace this MO2 mod folder
#                      (gamedata + mo2 + meta). Full builds only (not DOGMA_ONLY).
#   DOGMA_OUT=path     override output gamedata (default: build/gamedata). Set by
#                      package-fomod; skips wiping build/ and skips DOGMA_DEPLOY.
#   DOGMA_NO_ALAO=1    skip ALAO on src/ before staging (same tool/flags as Optimize)
#
# Default (no DOGMA_OUT): wipe build/, stage → build/, then optional full-replace
# deploy. package-fomod sets DOGMA_OUT and merges into its own stage dirs.
set -euo pipefail

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
trap 'rm -rf "$STAGE" "$STAGE_MODROOT"; rm -f "$MANIFEST" "$MANIFEST_MODROOT"' EXIT

if [[ ! -d "$SRC" ]]; then
	echo "build: missing $SRC" >&2
	exit 1
fi

# Same ALAO command as DOGMA Optimize, on local src/ only (--direct).
# alao_local.py clears *.alao-bak first so src/ is always re-fixed (not all-mods Optimize).
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
		*.alao-bak | *.pyc | *.pyo) return 0 ;;
		# Authoring / pack source only — shipped via db/mods/*.db0 instead.
		*.aimap) return 0 ;;
		# _conf: feature defaults. _dogma_input: early-load input ticker (before dogma_*).
		_conf.script | _dogma_input.script | _common | _debug) return 1 ;;
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
		_conf | mcm)
			echo "dogma_${path_key}_${stem#_}.script"
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
		fi
	else
		local cat="${parts[0]}"
		if is_gamedata_root "$cat"; then
			bucket_rel="$rel"
			path_key=""
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

	if [[ -n "$path_key" && "$base" == "mcm.script" ]]; then
		local conf_src="${src_path%/*}/_conf.script"
		if [[ -f "$conf_src" ]]; then
			{
				cat "$conf_src"
				echo ""
				echo "-- dogma-build: conf prepended so main-menu MCM (*mcm.script) has defaults"
				cat "$src_path"
			} > "$staged"
			printf '%s\n' "$dest_rel" >> "$MANIFEST"
			return 0
		fi
	fi

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
			# common + debug are stage:OMIT (no Setup/FOMOD) but still ship locally.
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
		[[ -d "$SRC/$ONLY_SRC_DIR" ]] || {
			echo "build: DOGMA_ONLY=$ONLY not found at $SRC/$ONLY_SRC_DIR" >&2
			exit 1
		}
		;;
esac

if [[ -n "$DEPLOY_MOD" && "$ONLY" != "all" && "$ONLY" != "" ]]; then
	echo "build: DOGMA_DEPLOY requires full build (DOGMA_ONLY=$ONLY would replace the whole mod)" >&2
	exit 1
fi

if [[ "$ONLY" == "all" || "$ONLY" == "" ]]; then
	dogma_load_manifest 1 || exit 1
	if ((${#FEATURES[@]} == 0)) && [[ -z "${DOGMA_ALLOW_EMPTY:-}" ]]; then
		echo "build: manifest yielded 0 features - refusing full build (fix YAML or set DOGMA_ALLOW_EMPTY=1)" >&2
		exit 1
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

run_alao_local || exit 1
echo "building..."

# Stage every shippable file (quiet).
# Prune authoring trees early - assets/ can hold multi-GB .blend/.mp4 that must
# never ship (map_src_file also rejects them; skipping the walk avoids Windows
# AV + bash path work on those files).
_emit_kind=""
while IFS= read -r -d '' src_path; do
	src_in_scope "$src_path" || continue
	map_src_file "$src_path" || continue
	stage_file "$_emit_kind" "$_emit_src" "$_emit_rel" "$_emit_path_key" "$_emit_base"
done < <(find "$SRC" \( -name assets -o -name installer -o -name __pycache__ \) -prune -o -type f -print0)

sort -u "$MANIFEST" -o "$MANIFEST"
sort -u "$MANIFEST_MODROOT" -o "$MANIFEST_MODROOT"

# Stage DOGMA MO2 config copies into the modroot stage (reference stays in repo config/).
# Live catalog: features.yml + mods.yml.
if [[ "$ONLY" == "all" || "$ONLY" == "" || "$ONLY" == "common" ]]; then
	MO2_CFG_STAGE="$STAGE_MODROOT/mo2/config"
	mkdir -p "$MO2_CFG_STAGE"
	for _cat in features.yml mods.yml manifest.yml manifest-third-party.yml manifest-dogma-features.yml manifest-dogma-tweaks.yml suggestions.yml mcm_config.yml; do
		if [[ -f "$ROOT/config/$_cat" ]]; then
			cp "$ROOT/config/$_cat" "$MO2_CFG_STAGE/$_cat"
			printf '%s\n' "mo2/config/$_cat" >> "$MANIFEST_MODROOT"
		fi
	done
	sort -u "$MANIFEST_MODROOT" -o "$MANIFEST_MODROOT"
fi

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
	write_mod_meta "$DEPLOY_MOD"
fi

echo "build: done ($count gamedata, $count_modroot modroot)"

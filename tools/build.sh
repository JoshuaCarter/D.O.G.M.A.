#!/usr/bin/env bash
# Compile src/ into gamedata for DOGMA.
#
# Layout (MCM-aligned):
#   src/common/<gamedata-rel>/...           -> <out>/<gamedata-rel>/...  (names kept)
#   src/<category>/<feature>/<gamedata-rel>/... -> <out>/<gamedata-rel>/...  (merged)
#
#   src/<category>/<feature>/assets/...     authoring only (ignored; not shipped)
#   src/<category>/<feature>/installer/...  FOMOD metadata (not shipped into gamedata)
#
# Scripts (prefix applied at build — src keeps short names like main.script):
#   common/scripts/*          -> same basename (dogma_common, dogma_mcm, …)
#                               no zzzz_ — always before every feature script
#   …/scripts/_conf.script    -> dogma_{path}_conf.script
#                               no zzzz_ — before that feature's zzzz_ body scripts
#   …/scripts/mcm.script      -> dogma_{path}_mcm.script   (*mcm.script glob)
#                               _conf is prepended so main-menu MCM (which only
#                               loads *mcm.script) still gets MOD_ID + defaults
#   …/scripts/modxml_*.script -> modxml_dogma_{path}_*.script
#                               keep modxml_ prefix — Modded Exes only gathers
#                               that glob for DXML on_xml_read injection
#   …/scripts/override/*.script -> scripts/<basename>.script  (exact name — replace
#                               conflicting mods: ASV, Free Zoom, etc.)
#   …/scripts/<other>.script  -> zzzz_dogma_{path}_<other>.script
#
# Env:
#   DOGMA_ONLY=spec    what to build:
#                        (empty|all)  → common + features with config/manifest.ini >= 1
#                        common       → common only
#                        cat/feat     → that feature only (e.g. zoom/free_zoom; ignores manifest)
#   DOGMA_DEPLOY=path  local MO2 mod folder: write gamedata straight there (one hop),
#                      plus meta.ini + .mod_id. Works with DOGMA_ONLY too (no prune).
#   DOGMA_OUT=path     override output gamedata (default: build/gamedata; disables
#                      the DOGMA_DEPLOY one-hop when set)
#
# Always stages every shippable file, then one bulk copy into OUT (no per-file
# log). Full builds prune stale outputs.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/src"
ONLY="${DOGMA_ONLY:-all}"

# shellcheck source=manifest_lib.sh
source "$ROOT/tools/manifest_lib.sh"

# Local MO2 deploy: one write path (full or DOGMA_ONLY). DOGMA_OUT wins for packaging.
if [[ -n "${DOGMA_DEPLOY:-}" && -z "${DOGMA_OUT:-}" ]]; then
	DEPLOY_MOD="${DOGMA_DEPLOY%/}"
	OUT="$DEPLOY_MOD/gamedata"
else
	DEPLOY_MOD=""
	OUT="${DOGMA_OUT:-$ROOT/build/gamedata}"
fi

GAMEDATA_ROOTS="scripts configs textures meshes anims sounds spawns materials"

STAGE="$(mktemp -d)"
MANIFEST="$(mktemp)"
trap 'rm -rf "$STAGE"; rm -f "$MANIFEST"' EXIT

if [[ ! -d "$SRC" ]]; then
	echo "build: missing $SRC" >&2
	exit 1
fi

should_skip_name() {
	local base="$1"
	case "$base" in
		README | README.* | MOVE_MAP | MOVE_MAP.* | .gitkeep | .DS_Store | Thumbs.db) return 0 ;;
		assets | installer) return 0 ;;
		*.alao-bak) return 0 ;;
		_conf.script) return 1 ;;
		_*) return 0 ;;
		*) return 1 ;;
	esac
}

is_gamedata_root() {
	local name="$1"
	local r
	for r in $GAMEDATA_ROOTS; do
		[[ "$name" == "$r" ]] && return 0
	done
	return 1
}

# path_key: category_feature (underscores). Empty = keep basename (common/).
script_dest_basename() {
	local path_key="$1"
	local src_base="$2"
	if [[ -z "$path_key" ]]; then
		echo "$src_base"
		return 0
	fi
	local stem="${src_base%.script}"
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

# Map one src file → dest_rel under gamedata. Sets: _emit_src _emit_rel _emit_path_key _emit_base
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
		esac
		if [[ "$part" == _* && "$part" != "$base" ]]; then
			return 1
		fi
	done

	local path_key="" bucket_rel=""

	if [[ "$rel" == common/* ]]; then
		bucket_rel="${rel#common/}"
		path_key=""
	else
		local cat="${parts[0]}"
		if is_gamedata_root "$cat"; then
			bucket_rel="$rel"
			path_key=""
		else
			(( ${#parts[@]} >= 4 )) || return 1
			local feat="${parts[1]}"
			local bucket="${parts[2]}"
			should_skip_name "$feat" && return 1
			is_gamedata_root "$bucket" || return 1
			path_key="${cat}_${feat}"
			bucket_rel="${rel#"$cat/$feat/"}"
		fi
	fi

	local bucket="${bucket_rel%%/*}"
	is_gamedata_root "$bucket" || return 1

	local dest_rel="$bucket_rel"
	if [[ "$bucket" == "scripts" && "$bucket_rel" == scripts/override/* ]]; then
		dest_rel="scripts/$base"
	elif [[ -n "$path_key" && "$base" == *.script && "$bucket" == "scripts" ]]; then
		local out_base
		out_base="$(script_dest_basename "$path_key" "$base")"
		if [[ "$bucket_rel" == */* ]]; then
			dest_rel="${bucket_rel%/*}/$out_base"
		else
			dest_rel="$out_base"
		fi
	fi

	_emit_src="$src_path"
	_emit_rel="$dest_rel"
	_emit_path_key="$path_key"
	_emit_base="$base"
	return 0
}

# Write into STAGE (correct layout). Manifest records relative path for prune.
stage_file() {
	local src_path="$1"
	local dest_rel="$2"
	local path_key="$3"
	local base="$4"
	local staged="$STAGE/$dest_rel"

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

src_in_scope() {
	local rel="${1#"$SRC"/}"
	rel="${rel//\\/\/}"
	case "$ONLY" in
		all | "")
			[[ "$rel" == common/* ]] && return 0
			local f
			for f in "${FEATURES[@]}"; do
				[[ "$rel" == "$f"/* || "$rel" == "$f" ]] && return 0
			done
			return 1
			;;
		common)
			[[ "$rel" == common/* ]]
			;;
		*/*)
			[[ "$rel" == "$ONLY"/* ]]
			;;
		*)
			return 1
			;;
	esac
}

mkdir -p "$OUT"
echo "build: out=$OUT"
if [[ -n "$DEPLOY_MOD" && "$ONLY" != "all" && "$ONLY" != "" ]]; then
	echo "build: DOGMA_ONLY=$ONLY → deploy $DEPLOY_MOD (no prune)"
fi

case "$ONLY" in
	all | "" | common | */*) ;;
	*)
		echo "build: bad DOGMA_ONLY=$ONLY (use all|common|category/feature)" >&2
		exit 1
		;;
esac

if [[ "$ONLY" == "all" || "$ONLY" == "" ]]; then
	dogma_load_manifest 1 || exit 1
	echo "build: config/manifest.ini local (${#FEATURES[@]} features)"
fi

if [[ "$ONLY" == */* && ! -d "$SRC/$ONLY" ]]; then
	echo "build: DOGMA_ONLY=$ONLY not found at $SRC/$ONLY" >&2
	exit 1
fi

# Stage every shippable file (quiet).
while IFS= read -r -d '' src_path; do
	src_in_scope "$src_path" || continue
	map_src_file "$src_path" || continue
	stage_file "$_emit_src" "$_emit_rel" "$_emit_path_key" "$_emit_base"
done < <(find "$SRC" -type f -print0)

sort -u "$MANIFEST" -o "$MANIFEST"
count="$(wc -l < "$MANIFEST" | tr -d ' ')"

# One bulk copy: stage → OUT.
cp -a "$STAGE"/. "$OUT"/

# Full build: drop outputs not produced this run.
PRUNED=0
if [[ "$ONLY" == "all" || "$ONLY" == "" ]]; then
	local_all="$(mktemp)"
	(
		cd "$OUT" && find . -type f | sed 's|^\./||' | sort
	) > "$local_all"
	while IFS= read -r rel; do
		[[ -z "$rel" ]] && continue
		rm -f "$OUT/$rel"
		PRUNED=$((PRUNED + 1))
	done < <(comm -23 "$local_all" "$MANIFEST")
	rm -f "$local_all"
fi

if (( PRUNED > 0 )); then
	echo "build: done ($count files, pruned $PRUNED)"
else
	echo "build: done ($count files)"
fi

# When writing straight into MO2, also refresh mod metadata.
# Use if/then (not `[[ -f ]] && cp`) so a missing optional file does not make
# the script exit 1 — that status is what build_and_run sees.
if [[ -n "$DEPLOY_MOD" ]]; then
	cp "$ROOT/meta.ini" "$DEPLOY_MOD/meta.ini"
	if [[ -f "$ROOT/.mod_id" ]]; then
		cp "$ROOT/.mod_id" "$DEPLOY_MOD/.mod_id"
	fi
	if [[ -f "$ROOT/INFO.md" ]]; then
		cp "$ROOT/INFO.md" "$DEPLOY_MOD/INFO.md"
	else
		rm -f "$DEPLOY_MOD/INFO.md"
	fi
fi

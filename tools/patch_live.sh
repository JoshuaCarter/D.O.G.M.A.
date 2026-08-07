#!/usr/bin/env bash
# Hot-deploy DOGMA assets into the MO2 mod folder (no full rebuild).
#
# Overwrites matching files under DOGMA_DEPLOY (default: C:/GAMMA/mods/DOGMA).
# Safe to run while the game is open. After deploy:
#   XML / text  → reopen the menu / MCM / UI (engine re-reads on open)
#   DDS / THM   → written to disk, but Anomaly keeps sheets in the resource
#                 cache (vid_restart does not help) — restart the game to see
# Scripts and most .ltx still need a game restart (not copied here).
#
# Deployed set (gamedata-relative):
#   configs/ui/**/*.xml          UI layouts + textures_descr
#   configs/text/**/*.xml        string tables + MCM text (ui_mcm_*.xml)
#   textures/**/*.{dds,thm}      UI / atlas sheets
#
# Env:
#   DOGMA_DEPLOY=path   MO2 mod root (must contain / become gamedata/)
#   DOGMA_ONLY=spec     same as build.sh (all|common|cat/feat|feat)
#   DOGMA_LIVE_DRY=1    list actions only
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/src"
ONLY="${DOGMA_ONLY:-all}"
DEPLOY_MOD="${DOGMA_DEPLOY:-C:/GAMMA/mods/DOGMA}"
DEPLOY_MOD="${DEPLOY_MOD%/}"
DRY="${DOGMA_LIVE_DRY:-}"

# shellcheck source=manifest_lib.sh
source "$ROOT/tools/manifest_lib.sh"

GAMEDATA_ROOTS="scripts configs textures meshes anims sounds spawns materials"
MODROOT_BUCKET="mo2"

if [[ ! -d "$SRC" ]]; then
	echo "patch_live: missing $SRC" >&2
	exit 1
fi

should_skip_name() {
	local base="$1"
	case "$base" in
		README | README.* | MOVE_MAP | MOVE_MAP.* | .gitkeep | .DS_Store | Thumbs.db) return 0 ;;
		assets | installer | __pycache__) return 0 ;;
		*.alao-bak | *.pyc | *.pyo) return 0 ;;
		_conf.script | _dogma_*.script | _common | _debug) return 1 ;;
		_*) return 0 ;;
		*) return 1 ;;
	esac
}

is_gamedata_root() {
	local name="$1" r
	for r in $GAMEDATA_ROOTS; do
		[[ "$name" == "$r" ]] && return 0
	done
	return 1
}

src_feature_dir() {
	case "$1" in
		common) echo "_common" ;;
		debug) echo "_debug" ;;
		*) echo "$1" ;;
	esac
}

src_dir_to_feature() {
	case "$1" in
		_common) echo "common" ;;
		_debug) echo "debug" ;;
		*) echo "$1" ;;
	esac
}

script_dest_basename() {
	local path_key="$1" src_base="$2"
	if [[ -z "$path_key" ]]; then
		echo "$src_base"
		return 0
	fi
	local stem="${src_base%.script}"
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

# Same mapping rules as tools/build.sh (gamedata + modroot).
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
			_common | _debug) ;;
			_*)
				[[ "$part" == "$base" ]] || return 1
				;;
		esac
	done

	local path_key="" bucket_rel=""

	if [[ "$rel" == _common/* ]]; then
		bucket_rel="${rel#_common/}"
		path_key=""
		local common_bucket="${bucket_rel%%/*}"
		if [[ "$common_bucket" == "$MODROOT_BUCKET" ]]; then
			return 1 # mo2 tools — not live in-game assets
		fi
	else
		local cat="${parts[0]}"
		if is_gamedata_root "$cat"; then
			bucket_rel="$rel"
			path_key=""
		elif (( ${#parts[@]} >= 3 )) && is_gamedata_root "${parts[1]}"; then
			local feat_dir="$cat"
			local feat
			feat="$(src_dir_to_feature "$feat_dir")"
			local bucket="${parts[1]}"
			should_skip_name "$feat_dir" && return 1
			path_key="$feat"
			if [[ "$bucket" == "$MODROOT_BUCKET" ]]; then
				return 1
			fi
			is_gamedata_root "$bucket" || return 1
			bucket_rel="${rel#"$feat_dir/"}"
		else
			(( ${#parts[@]} >= 4 )) || return 1
			local feat="${parts[1]}"
			local bucket="${parts[2]}"
			should_skip_name "$feat" && return 1
			path_key="${cat}_${feat}"
			if [[ "$bucket" == "$MODROOT_BUCKET" ]]; then
				return 1
			fi
			is_gamedata_root "$bucket" || return 1
			bucket_rel="${rel#"$cat/$feat/"}"
		fi
	fi

	local bucket="${bucket_rel%%/*}"
	is_gamedata_root "$bucket" || return 1

	local dest_rel="$bucket_rel"
	if [[ -n "$path_key" && "$base" == *.script && "$bucket" == "scripts" ]]; then
		if [[ "$bucket_rel" == scripts/override/* ]]; then
			dest_rel="scripts/$base"
		else
			dest_rel="scripts/$(script_dest_basename "$path_key" "$base")"
		fi
	fi

	_emit_src="$src_path"
	_emit_rel="$dest_rel"
	_emit_base="$base"
	return 0
}

# Included in hot deploy. XML reloads on UI open; DDS need a game restart to show.
is_live_asset() {
	local rel="$1"
	case "$rel" in
		scripts/*) return 1 ;;
	esac
	case "$rel" in
		configs/ui/*.xml | configs/ui/*/*.xml | configs/ui/*/*/*.xml | configs/ui/*/*/*/*.xml) return 0 ;;
		configs/text/*.xml | configs/text/*/*.xml | configs/text/*/*/*.xml) return 0 ;;
		textures/*.dds | textures/*/*.dds | textures/*/*/*.dds | textures/*/*/*/*.dds) return 0 ;;
		textures/*.thm | textures/*/*.thm | textures/*/*/*.thm | textures/*/*/*/*.thm) return 0 ;;
	esac
	# Bash globs only one * per segment — deeper trees:
	if [[ "$rel" == configs/ui/* && "$rel" == *.xml ]]; then
		return 0
	fi
	if [[ "$rel" == configs/text/* && "$rel" == *.xml ]]; then
		return 0
	fi
	if [[ "$rel" == textures/* && ( "$rel" == *.dds || "$rel" == *.thm ) ]]; then
		return 0
	fi
	return 1
}

FEATURE_SRC_DIRS=()
ONLY_SRC_DIR=""

src_in_scope() {
	local rel="${1#"$SRC"/}"
	rel="${rel//\\/\/}"
	local sdir
	case "$ONLY" in
		all | "")
			[[ "$rel" == _common/* ]] && return 0
			for sdir in "${FEATURE_SRC_DIRS[@]}"; do
				[[ "$rel" == "$sdir"/* || "$rel" == "$sdir" ]] && return 0
			done
			return 1
			;;
		common)
			[[ "$rel" == _common/* ]]
			;;
		*)
			[[ "$rel" == "$ONLY_SRC_DIR"/* || "$rel" == "$ONLY_SRC_DIR" ]]
			;;
	esac
}

case "$ONLY" in
	all | "" | common) ;;
	*)
		ONLY_SRC_DIR="$(src_feature_dir "$ONLY")"
		[[ -d "$SRC/$ONLY_SRC_DIR" ]] || {
			echo "patch_live: DOGMA_ONLY=$ONLY not found at $SRC/$ONLY_SRC_DIR" >&2
			exit 1
		}
		;;
esac

if [[ "$ONLY" == "all" || "$ONLY" == "" ]]; then
	dogma_load_manifest 1 || exit 1
	FEATURE_SRC_DIRS=()
	for f in "${FEATURES[@]}"; do
		FEATURE_SRC_DIRS+=("$(src_feature_dir "$f")")
	done
fi

mkdir -p "$DEPLOY_MOD/gamedata"
echo "patch_live: deploy=$DEPLOY_MOD/gamedata"
echo "patch_live: hot deploy scope=$ONLY (XML/DDS/THM; no scripts)"

copied=0
copied_dds=0
skipped=0
_emit_src=""
_emit_rel=""
_emit_base=""

while IFS= read -r -d '' src_path; do
	src_in_scope "$src_path" || continue
	map_src_file "$src_path" || continue
	is_live_asset "$_emit_rel" || continue
	dest="$DEPLOY_MOD/gamedata/$_emit_rel"
	if [[ -f "$dest" ]] && cmp -s "$src_path" "$dest"; then
		skipped=$((skipped + 1))
		continue
	fi
	if [[ -n "$DRY" ]]; then
		echo "  would copy $_emit_rel"
		copied=$((copied + 1))
		case "$_emit_rel" in
			*.dds | *.thm) copied_dds=$((copied_dds + 1)) ;;
		esac
		continue
	fi
	mkdir -p "$(dirname "$dest")"
	cp "$src_path" "$dest"
	echo "  $_emit_rel"
	copied=$((copied + 1))
	case "$_emit_rel" in
		*.dds | *.thm) copied_dds=$((copied_dds + 1)) ;;
	esac
done < <(find "$SRC" \( -name assets -o -name installer -o -name __pycache__ \) -prune -o -type f \( -name '*.xml' -o -name '*.dds' -o -name '*.thm' \) -print0)

echo "patch_live: done (wrote $copied, unchanged $skipped)"
echo "patch_live: XML/text → reopen UI/MCM · scripts still need full Build + restart"
if [[ "$copied_dds" -gt 0 ]]; then
	echo "patch_live: DDS/THM ($copied_dds) → restart game to see (vid_restart does not reload)"
fi

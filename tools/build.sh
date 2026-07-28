#!/usr/bin/env bash
# Compile src/ into gamedata for DOGMA.
#
# Layout (MCM-aligned):
#   src/common/<gamedata-rel>/...           -> <out>/<gamedata-rel>/...  (names kept)
#   src/<category>/<feature>/<gamedata-rel>/... -> <out>/<gamedata-rel>/...  (merged)
#
#   src/<category>/<feature>/assets/...     authoring only (ignored; not shipped)
#   src/<category>/<feature>/installer/image.png  optional FOMOD hover image (not shipped into gamedata)
#   src/common/mo2/...                      EXCEPTION: files under <mod>/mo2/
#   src/<category>/<feature>/mo2/...        (sibling of gamedata/), e.g. mo2/tools/…
#
# Scripts (prefix applied at build — src keeps short names like main.script):
#   common/scripts/*          -> same basename (dogma_common, dogma_mcm,
#                               dogma_key_mirror_mcm, …)
#                               no zzzz_ — always before every feature script
#                               *mcm.script also participates in MCM gather
#                               (key mirrors live in dogma_key_mirror_mcm)
#   …/scripts/_conf.script    -> dogma_{path}_conf.script
#                               no zzzz_ — before that feature's zzzz_ body scripts
#   …/scripts/mcm.script      -> dogma_{path}_mcm.script   (*mcm.script glob)
#                               _conf is prepended so main-menu MCM (which only
#                               loads *mcm.script) still gets MOD_ID + defaults
#   …/scripts/modxml_*.script -> modxml_dogma_{path}_*.script
#                               keep modxml_ prefix — Modded Exes only gathers
#                               that glob for DXML on_xml_read injection
#   …/scripts/override/*.script -> scripts/<basename>.script  (exact name — only
#                               when disabled.ini cannot cover the conflict:
#                               exo MCM replace, blank vanilla game_fast_travel,
#                               Blindside/Keybinds scripts we must not disable)
#   …/scripts/**/*.script     -> scripts/zzzz_dogma_{path}_<stem>.script
#
# Env:
#   DOGMA_ONLY=spec    what to build:
#                        (empty|all)  → common + features with config/features.yml >= local
#                        common       → common only
#                        cat/feat     → that feature only (e.g. zoom/free_zoom; ignores manifest)
#   DOGMA_DEPLOY=path  local MO2 mod folder: write gamedata straight there (one hop),
#                      plus meta.ini + .mod_id + mo2/ mod-root files. Works with
#                      DOGMA_ONLY too (no prune).
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
	MODROOT_OUT="$DEPLOY_MOD"
else
	DEPLOY_MOD=""
	OUT="${DOGMA_OUT:-$ROOT/build/gamedata}"
	MODROOT_OUT="$(cd "$(dirname "$OUT")" && pwd)"
fi

GAMEDATA_ROOTS="scripts configs textures meshes anims sounds spawns materials"
# Feature bucket that ships next to gamedata/ (MO2 mod root), not into gamedata.
MODROOT_BUCKET="mo2"

STAGE="$(mktemp -d)"
STAGE_MODROOT="$(mktemp -d)"
MANIFEST="$(mktemp)"
MANIFEST_MODROOT="$(mktemp)"
trap 'rm -rf "$STAGE" "$STAGE_MODROOT"; rm -f "$MANIFEST" "$MANIFEST_MODROOT"' EXIT

if [[ ! -d "$SRC" ]]; then
	echo "build: missing $SRC" >&2
	exit 1
fi

should_skip_name() {
	local base="$1"
	case "$base" in
		README | README.* | MOVE_MAP | MOVE_MAP.* | .gitkeep | .DS_Store | Thumbs.db) return 0 ;;
		assets | installer | __pycache__) return 0 ;;
		*.alao-bak | *.pyc | *.pyo) return 0 ;;
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
		esac
		if [[ "$part" == _* && "$part" != "$base" ]]; then
			return 1
		fi
	done

	local path_key="" bucket_rel=""

	if [[ "$rel" == common/* ]]; then
		bucket_rel="${rel#common/}"
		path_key=""
		# EXCEPTION: common/mo2/ → <MO2 mod>/mo2/ (always-on core tools).
		local common_bucket="${bucket_rel%%/*}"
		if [[ "$common_bucket" == "$MODROOT_BUCKET" ]]; then
			bucket_rel="${bucket_rel#"$MODROOT_BUCKET"/}"
			[[ -n "$bucket_rel" && "$bucket_rel" != "$common_bucket" ]] || return 1
			_emit_kind="modroot"
			_emit_src="$src_path"
			_emit_rel="$MODROOT_BUCKET/$bucket_rel"
			_emit_path_key=""
			_emit_base="$base"
			return 0
		fi
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
			path_key="${cat}_${feat}"

			# EXCEPTION: mo2/ → <MO2 mod>/mo2/ (sibling of gamedata/), paths kept under mo2/.
			if [[ "$bucket" == "$MODROOT_BUCKET" ]]; then
				bucket_rel="${rel#"$cat/$feat/$MODROOT_BUCKET/"}"
				[[ -n "$bucket_rel" ]] || return 1
				_emit_kind="modroot"
				_emit_src="$src_path"
				_emit_rel="$MODROOT_BUCKET/$bucket_rel"
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
mkdir -p "$MODROOT_OUT"
echo "build: out=$OUT"
echo "build: modroot=$MODROOT_OUT"
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
	if ((${#FEATURES[@]} == 0)) && [[ -z "${DOGMA_ALLOW_EMPTY:-}" ]]; then
		echo "build: manifest yielded 0 features — refusing full build/prune (fix YAML or set DOGMA_ALLOW_EMPTY=1)" >&2
		exit 1
	fi
	echo "build: config/manifest.yml stage>=dev (${#FEATURES[@]} features)"
fi

if [[ "$ONLY" == */* && ! -d "$SRC/$ONLY" ]]; then
	echo "build: DOGMA_ONLY=$ONLY not found at $SRC/$ONLY" >&2
	exit 1
fi

# Stage every shippable file (quiet).
_emit_kind=""
while IFS= read -r -d '' src_path; do
	src_in_scope "$src_path" || continue
	map_src_file "$src_path" || continue
	stage_file "$_emit_kind" "$_emit_src" "$_emit_rel" "$_emit_path_key" "$_emit_base"
done < <(find "$SRC" -type f -print0)

sort -u "$MANIFEST" -o "$MANIFEST"
sort -u "$MANIFEST_MODROOT" -o "$MANIFEST_MODROOT"

# Stage DOGMA MO2 config copies into the modroot stage (reference stays in repo config/).
# Live catalog: features.yml + mods.yml.
if [[ "$ONLY" == "all" || "$ONLY" == "" || "$ONLY" == "common" ]]; then
	MO2_CFG_STAGE="$STAGE_MODROOT/mo2/config"
	mkdir -p "$MO2_CFG_STAGE"
	for _cat in features.yml mods.yml manifest.yml suggestions.yml; do
		if [[ -f "$ROOT/config/$_cat" ]]; then
			cp "$ROOT/config/$_cat" "$MO2_CFG_STAGE/$_cat"
			printf '%s\n' "mo2/config/$_cat" >> "$MANIFEST_MODROOT"
		fi
	done
	sort -u "$MANIFEST_MODROOT" -o "$MANIFEST_MODROOT"
fi

count="$(wc -l < "$MANIFEST" | tr -d ' ')"
count_modroot="$(wc -l < "$MANIFEST_MODROOT" | tr -d ' ')"

# One bulk copy: stage → OUT / mod root.
cp -a "$STAGE"/. "$OUT"/
if [[ "$count_modroot" != "0" ]]; then
	cp -a "$STAGE_MODROOT"/. "$MODROOT_OUT"/
fi

# Full build: drop gamedata outputs not produced this run (keep generated LTX).
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

	# Mod-root: prune any mo2/** file on disk that this run did not ship.
	# Never touch non-mo2/ mod-root files.
	if [[ -d "$MODROOT_OUT/mo2" ]]; then
		mo2_all="$(mktemp)"
		(
			cd "$MODROOT_OUT" && find mo2 -type f | sed 's|^\./||' | sort
		) > "$mo2_all"
		while IFS= read -r rel; do
			[[ -z "$rel" ]] && continue
			# Feature zips from FOMOD packaging — keep across local merge deploys.
			case "$rel" in
				mo2/packages/*) continue ;;
			esac
			if ! grep -Fxq "$rel" "$MANIFEST_MODROOT"; then
				rm -f "$MODROOT_OUT/$rel"
				PRUNED=$((PRUNED + 1))
			fi
		done < <(comm -23 "$mo2_all" "$MANIFEST_MODROOT")
		rm -f "$mo2_all"
		# Drop flat copies / old layout leftovers.
		rm -f "$MODROOT_OUT/build_sound_prefetch.bat" "$MODROOT_OUT/build_sound_prefetch.py"
		rm -f "$MODROOT_OUT/mo2/build_sound_prefetch.bat" "$MODROOT_OUT/mo2/build_sound_prefetch.py"
		rm -f "$MODROOT_OUT/mo2/prelaunch.bat" "$MODROOT_OUT/mo2/tools/prelaunch.py" \
			"$MODROOT_OUT/mo2/tools/DOGMA.bat" "$MODROOT_OUT/mo2/tools/run_job.bat" \
			"$MODROOT_OUT/mo2/tools/setup.bat"
	fi
fi

if (( PRUNED > 0 )); then
	echo "build: done ($count gamedata, $count_modroot modroot, pruned $PRUNED)"
else
	echo "build: done ($count gamedata, $count_modroot modroot)"
fi

# When writing straight into MO2, also refresh mod metadata.
# Use if/then (not `[[ -f ]] && cp`) so a missing optional file does not make
# the script exit 1 under `set -e`.
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
	# Drop leftover launcher bits from earlier layouts.
	rm -rf "$DEPLOY_MOD/sound_prefetch"
	rm -f "$DEPLOY_MOD/gamedata/scripts/dogma_snd_prefetch.script"
	rm -f "$DEPLOY_MOD/gamedata/configs/items/items/dogma_snd_prefetch.ltx"
	rm -f "$DEPLOY_MOD/gamedata/configs/dogma_snd_prefetch.ltx"
	rm -f "$DEPLOY_MOD/gamedata/configs/dogma_sfx_prefetch.ltx"
	rm -f "$DEPLOY_MOD/build_sound_prefetch.bat" "$DEPLOY_MOD/build_sound_prefetch.py"
	rm -f "$DEPLOY_MOD/mo2/build_sound_prefetch.bat" "$DEPLOY_MOD/mo2/build_sound_prefetch.py"
	rm -f "$DEPLOY_MOD/mo2/prelaunch.bat" "$DEPLOY_MOD/mo2/tools/prelaunch.py" \
		"$DEPLOY_MOD/mo2/tools/DOGMA.bat" "$DEPLOY_MOD/mo2/tools/run_job.bat" \
		"$DEPLOY_MOD/mo2/tools/setup.bat"
fi

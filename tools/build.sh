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
#   …/scripts/override/*.script -> scripts/<basename>.script  (exact name — replace
#                               conflicting mods: ASV, Free Zoom, etc.)
#   …/scripts/<other>.script  -> zzzz_dogma_{path}_<other>.script
#
# Env:
#   DOGMA_ONLY=spec    what to build:
#                        (empty|all)  → common + every feature
#                        common       → common only
#                        cat/feat     → that feature only (e.g. zoom/free_zoom)
#   DOGMA_DEPLOY=path  local MO2 mod folder: write gamedata straight there (one hop),
#                      plus meta.ini + .mod_id. Skips files whose mtime is current.
#   DOGMA_OUT=path     override output gamedata (default: build/gamedata; disables
#                      the DOGMA_DEPLOY one-hop when set)
#
# Unchanged files skipped via mtime only (no byte cmp — textures made that slow).
# One find of src/ — no per-directory find/stat spawns.
# Stale outputs pruned on full builds.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/src"
ONLY="${DOGMA_ONLY:-all}"

# Local MO2 deploy: one write path. Otherwise default build/gamedata (packaging).
if [[ -n "${DOGMA_DEPLOY:-}" && ( "$ONLY" == "all" || "$ONLY" == "" ) && -z "${DOGMA_OUT:-}" ]]; then
	DEPLOY_MOD="${DOGMA_DEPLOY%/}"
	OUT="$DEPLOY_MOD/gamedata"
else
	DEPLOY_MOD=""
	OUT="${DOGMA_OUT:-$ROOT/build/gamedata}"
fi

GAMEDATA_ROOTS="scripts configs textures meshes anims sounds spawns"

COPIED=0
SKIPPED=0
MANIFEST="$(mktemp)"
trap 'rm -f "$MANIFEST"' EXIT

if [[ ! -d "$SRC" ]]; then
	echo "build: missing $SRC" >&2
	exit 1
fi

# Dest exists and is not older than src → skip. No size/cmp (spawn-heavy on Win).
up_to_date() {
	[[ -f "$2" ]] && ! [[ "$1" -nt "$2" ]]
}

note_manifest() {
	local dest="$1"
	local rel="${dest#"$OUT"/}"
	printf '%s\n' "${rel#/}" >> "$MANIFEST"
}

copy_if_changed() {
	local src="$1"
	local dest="$2"
	note_manifest "$dest"
	if up_to_date "$src" "$dest"; then
		SKIPPED=$((SKIPPED + 1))
		return 0
	fi
	mkdir -p "${dest%/*}"
	cp "$src" "$dest"
	COPIED=$((COPIED + 1))
	echo "build: copy  ${src#"$ROOT"/} -> ${dest#"$OUT"/}"
}

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
	esac
	echo "zzzz_dogma_${path_key}_${stem}.script"
}

# Map one src file → dest under OUT. Prints nothing / returns 1 if not shipped.
# Sets globals: _emit_src _emit_dest _emit_path_key (path_key empty for common)
map_src_file() {
	local src_path="$1"
	local rel="${src_path#"$SRC"/}"
	rel="${rel//\\/\/}"
	local base="${src_path##*/}"

	should_skip_name "$base" && return 1

	# Skip any path segment that is authoring-only or private.
	local part
	IFS=/ read -r -a parts <<< "$rel"
	for part in "${parts[@]}"; do
		case "$part" in
			assets | installer) return 1 ;;
		esac
		# Private dirs (not _conf.script file — that's the basename check above).
		if [[ "$part" == _* && "$part" != "$base" ]]; then
			return 1
		fi
	done

	local path_key="" bucket_rel=""

	if [[ "$rel" == common/* ]]; then
		bucket_rel="${rel#common/}"
		path_key=""
	else
		# category/feature/bucket/...  OR  top-level gamedata root under src/
		local cat="${parts[0]}"
		if is_gamedata_root "$cat"; then
			bucket_rel="$rel"
			path_key=""
		else
			# need at least cat/feat/bucket/file
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
	# Exact-name overrides: scripts/override/foo.script → scripts/foo.script
	# (replace conflicting mods in MO2; no dogma_ rename).
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
	_emit_dest="$OUT/$dest_rel"
	_emit_path_key="$path_key"
	_emit_base="$base"
	return 0
}

emit_mapped() {
	local src_path="$_emit_src"
	local dest="$_emit_dest"
	local path_key="$_emit_path_key"
	local base="$_emit_base"

	# Main-menu MCM only loads *mcm.script — prepend sibling _conf so CONF exists.
	if [[ -n "$path_key" && "$base" == "mcm.script" ]]; then
		local conf_src
		conf_src="${src_path%/*}/_conf.script"
		if [[ -f "$conf_src" ]]; then
			note_manifest "$dest"
			if [[ -f "$dest" ]] \
				&& ! [[ "$conf_src" -nt "$dest" ]] \
				&& ! [[ "$src_path" -nt "$dest" ]]; then
				SKIPPED=$((SKIPPED + 1))
				return 0
			fi
			mkdir -p "${dest%/*}"
			{
				cat "$conf_src"
				echo ""
				echo "-- dogma-build: conf prepended so main-menu MCM (*mcm.script) has defaults"
				cat "$src_path"
			} > "$dest"
			COPIED=$((COPIED + 1))
			echo "build: copy  ${src_path#"$ROOT"/} (+_conf) -> ${dest#"$OUT"/}"
			return 0
		fi
	fi

	copy_if_changed "$src_path" "$dest"
}

src_in_scope() {
	local rel="${1#"$SRC"/}"
	rel="${rel//\\/\/}"
	case "$ONLY" in
		all | "") return 0 ;;
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

case "$ONLY" in
	all | "" | common | */*) ;;
	*)
		echo "build: bad DOGMA_ONLY=$ONLY (use all|common|category/feature)" >&2
		exit 1
		;;
esac

if [[ "$ONLY" == */* && ! -d "$SRC/$ONLY" ]]; then
	echo "build: DOGMA_ONLY=$ONLY not found at $SRC/$ONLY" >&2
	exit 1
fi

# One walk of src — maps every shippable file. No nested find per directory.
while IFS= read -r -d '' src_path; do
	src_in_scope "$src_path" || continue
	map_src_file "$src_path" || continue
	emit_mapped
done < <(find "$SRC" -type f -print0)

# Drop outputs this build did not produce (renames / removed features).
if [[ "$ONLY" == "all" || "$ONLY" == "" ]]; then
	sort -u "$MANIFEST" -o "$MANIFEST"
	local_all="$(mktemp)"
	(
		cd "$OUT" && find . -type f | sed 's|^\./||' | sort
	) > "$local_all"
	while IFS= read -r rel; do
		[[ -z "$rel" ]] && continue
		rm -f "$OUT/$rel"
		echo "build: prune $rel"
	done < <(comm -23 "$local_all" "$MANIFEST")
	rm -f "$local_all"
fi

count="$(wc -l < "$MANIFEST" | tr -d ' ')"
echo "build: done ($count files under $OUT; copied=$COPIED skipped=$SKIPPED)"

# When writing straight into MO2, also refresh mod metadata if needed.
if [[ -n "$DEPLOY_MOD" ]]; then
	if ! up_to_date "$ROOT/meta.ini" "$DEPLOY_MOD/meta.ini"; then
		cp "$ROOT/meta.ini" "$DEPLOY_MOD/meta.ini"
		echo "build: copy  meta.ini"
	fi
	if [[ -f "$ROOT/.mod_id" ]] && ! up_to_date "$ROOT/.mod_id" "$DEPLOY_MOD/.mod_id"; then
		cp "$ROOT/.mod_id" "$DEPLOY_MOD/.mod_id"
		echo "build: copy  .mod_id"
	fi
	if [[ -f "$ROOT/INFO.md" ]] && ! up_to_date "$ROOT/INFO.md" "$DEPLOY_MOD/INFO.md"; then
		cp "$ROOT/INFO.md" "$DEPLOY_MOD/INFO.md"
		echo "build: copy  INFO.md"
	fi
fi

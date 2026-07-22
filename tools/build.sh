#!/usr/bin/env bash
# Compile src/ into build/gamedata/ for DOGMA.
#
# Layout (MCM-aligned):
#   src/common/<gamedata-rel>/...           -> build/gamedata/<gamedata-rel>/...  (names kept)
#   src/<category>/<feature>/<gamedata-rel>/... -> build/gamedata/<gamedata-rel>/...  (merged)
#
#   src/<category>/<feature>/assets/...   authoring only (ignored; not shipped)
#
# Scripts (prefix applied at build — src keeps short names like main.script):
#   common/scripts/*          -> same basename (dorn_common, dorn_mcm, …)
#   …/scripts/mcm.script      -> dogma_{path}_mcm.script   (no zzzz_; still *mcm.script)
#   …/scripts/<other>.script  -> zzzz_dogma_{path}_<other>.script
# Path key = dirs under src up to (not including) the gamedata root, joined with _.
#
# Conflict (two sources -> same dest) is a hard error.
#
# Wipes build/gamedata/ then rebuilds from src/.
# Optional: DOGMA_DEPLOY=/path/to/MO2/mods/DOGMA  (copies gamedata + meta.ini + .mod_id)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/src"
OUT="$ROOT/build/gamedata"

GAMEDATA_ROOTS="scripts configs textures meshes anims sounds spawns"

if [[ ! -d "$SRC" ]]; then
	echo "build: missing $SRC" >&2
	exit 1
fi

should_skip_name() {
	local base="$1"
	case "$base" in
		README | README.* | MOVE_MAP | MOVE_MAP.* | .gitkeep | .DS_Store | Thumbs.db) return 0 ;;
		assets) return 0 ;; # authoring only (blend/pdn/mp4/png); not shipped
		*.alao-bak) return 0 ;;
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
# mcm.script -> dogma_{path}_mcm.script (no zzzz_; MCM glob is *mcm.script).
# everything else -> zzzz_dogma_{path}_{stem}.script
script_dest_basename() {
	local path_key="$1"
	local src_base="$2"
	if [[ -z "$path_key" ]]; then
		echo "$src_base"
		return 0
	fi
	local stem="${src_base%.script}"
	if [[ "$stem" == "mcm" ]]; then
		echo "dogma_${path_key}_mcm.script"
		return 0
	fi
	echo "zzzz_dogma_${path_key}_${stem}.script"
}

if [[ -d "$OUT" ]]; then
	rm -rf "$OUT"
fi
mkdir -p "$OUT"

smush_dir() {
	local dir="$1"
	local dest="$2"
	local tmp parts=0
	tmp="$(mktemp)"
	{
		echo "-- dogma-build: smushed from ${dir#"$ROOT"/}"
		while IFS= read -r -d '' part; do
			base="$(basename "$part")"
			should_skip_name "$base" && continue
			[[ -f "$part" ]] || continue
			parts=$((parts + 1))
			echo ""
			echo "-- dogma-build: begin $base"
			cat "$part"
			echo ""
			echo "-- dogma-build: end $base"
		done < <(find "$dir" -maxdepth 1 -type f -print0 | sort -z)
	} > "$tmp"
	if [[ "$parts" -eq 0 ]]; then
		rm -f "$tmp"
		echo "build: skip empty smush dir ${dir#"$ROOT"/}" >&2
		return 0
	fi
	mkdir -p "$(dirname "$dest")"
	if [[ -e "$dest" ]]; then
		echo "build: ERROR naming conflict: $dest already exists (smush from ${dir#"$ROOT"/})" >&2
		rm -f "$tmp"
		exit 1
	fi
	mv "$tmp" "$dest"
	echo "build: smush ${dir#"$ROOT"/} -> ${dest#"$ROOT"/} ($parts parts)"
}

# Copy/smush tree under src_base into dest_base (preserving relative paths).
# path_key: if non-empty and bucket is scripts/, rewrite .script basenames.
process_tree() {
	local src_base="$1"
	local dest_base="$2"
	local src_path="$3"
	local path_key="${4:-}"

	local rel="${src_path#"$src_base"/}"
	rel="${rel//\\/\/}"
	local base
	base="$(basename "$src_path")"

	if [[ -f "$src_path" ]]; then
		should_skip_name "$base" && return 0
		local out_base="$base"
		local dest_rel="$rel"
		if [[ -n "$path_key" && "$base" == *.script ]]; then
			out_base="$(script_dest_basename "$path_key" "$base")"
			# rel may be nested under scripts/; only rewrite the final basename
			if [[ "$rel" == */* ]]; then
				dest_rel="$(dirname "$rel")/$out_base"
			else
				dest_rel="$out_base"
			fi
		fi
		local dest="$dest_base/$dest_rel"
		mkdir -p "$(dirname "$dest")"
		if [[ -e "$dest" ]]; then
			echo "build: ERROR naming conflict: $dest (from ${src_path#"$ROOT"/})" >&2
			exit 1
		fi
		cp "$src_path" "$dest"
		echo "build: copy  ${src_path#"$ROOT"/} -> ${dest#"$ROOT"/}"
		return 0
	fi

	if [[ -d "$src_path" ]]; then
		if [[ "$base" == *.* ]]; then
			local out_base="$base"
			local dest_rel="$rel"
			if [[ -n "$path_key" && "$base" == *.script ]]; then
				out_base="$(script_dest_basename "$path_key" "$base")"
				if [[ "$rel" == */* ]]; then
					dest_rel="$(dirname "$rel")/$out_base"
				else
					dest_rel="$out_base"
				fi
			fi
			local dest="$dest_base/$dest_rel"
			smush_dir "$src_path" "$dest"
			return 0
		fi
		while IFS= read -r -d '' child; do
			process_tree "$src_base" "$dest_base" "$child" "$path_key"
		done < <(find "$src_path" -mindepth 1 -maxdepth 1 -print0 | sort -z)
	fi
}

# bucket_src e.g. src/mutants/pseudogiant/scripts
# path_key e.g. mutants_pseudogiant (empty for common)
merge_gamedata_bucket() {
	local bucket_src="$1"
	local path_key="${2:-}"
	local bucket_name
	bucket_name="$(basename "$bucket_src")"
	# Only scripts get the dogma filename prefix; configs/textures keep names.
	local key_for_tree=""
	if [[ "$bucket_name" == "scripts" ]]; then
		key_for_tree="$path_key"
	fi
	process_tree "$bucket_src" "$OUT/$bucket_name" "$bucket_src" "$key_for_tree"
}

# path_key from path relative to src, stopping before the gamedata root.
# src/mutants/pseudogiant/scripts -> mutants_pseudogiant
path_key_for_bucket() {
	local bucket_src="$1"
	local rel="${bucket_src#"$SRC"/}"
	rel="${rel//\\/\/}"
	local parent
	parent="$(dirname "$rel")"
	if [[ "$parent" == "." || "$parent" == "common" ]]; then
		echo ""
		return 0
	fi
	echo "${parent//\//_}"
}

# --- common/ (vendored Dorns_Common) — keep script names ---
if [[ -d "$SRC/common" ]]; then
	while IFS= read -r -d '' child; do
		base="$(basename "$child")"
		should_skip_name "$base" && continue
		if is_gamedata_root "$base"; then
			merge_gamedata_bucket "$child" ""
		elif [[ -d "$child" ]]; then
			echo "build: skip non-gamedata under common/: $base" >&2
		fi
	done < <(find "$SRC/common" -mindepth 1 -maxdepth 1 -print0 | sort -z)
fi

# --- category/feature/ ---
while IFS= read -r -d '' cat_dir; do
	[[ -d "$cat_dir" ]] || continue
	cat_base="$(basename "$cat_dir")"
	should_skip_name "$cat_base" && continue
	[[ "$cat_base" == "common" ]] && continue
	# legacy flat gamedata roots at src/ top — still supported (no prefix)
	if is_gamedata_root "$cat_base"; then
		merge_gamedata_bucket "$cat_dir" ""
		continue
	fi

	while IFS= read -r -d '' feat_dir; do
		[[ -d "$feat_dir" ]] || continue
		feat_base="$(basename "$feat_dir")"
		should_skip_name "$feat_base" && continue

		while IFS= read -r -d '' bucket; do
			b="$(basename "$bucket")"
			should_skip_name "$b" && continue
			if is_gamedata_root "$b"; then
				pk="$(path_key_for_bucket "$bucket")"
				merge_gamedata_bucket "$bucket" "$pk"
			elif [[ -d "$bucket" ]]; then
				echo "build: skip non-gamedata under ${cat_base}/${feat_base}/: $b" >&2
			elif [[ -f "$bucket" ]]; then
				echo "build: skip loose file under ${cat_base}/${feat_base}/: $b (must live under a gamedata root)" >&2
			fi
		done < <(find "$feat_dir" -mindepth 1 -maxdepth 1 -print0 | sort -z)
	done < <(find "$cat_dir" -mindepth 1 -maxdepth 1 -print0 | sort -z)
done < <(find "$SRC" -mindepth 1 -maxdepth 1 -print0 | sort -z)

count="$(find "$OUT" -type f 2>/dev/null | wc -l | tr -d ' ')"
echo "build: done ($count files under build/gamedata/)"

if [[ -n "${DOGMA_DEPLOY:-}" ]]; then
	dest="${DOGMA_DEPLOY%/}"
	mkdir -p "$dest"
	rm -rf "$dest/gamedata"
	cp -a "$OUT" "$dest/gamedata"
	cp -a "$ROOT/meta.ini" "$dest/meta.ini"
	if [[ -f "$ROOT/.mod_id" ]]; then
		cp -a "$ROOT/.mod_id" "$dest/.mod_id"
	fi
	echo "build: deployed to $dest (gamedata + meta.ini + .mod_id)"
fi

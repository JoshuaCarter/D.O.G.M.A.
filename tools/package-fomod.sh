#!/usr/bin/env bash
# Build a FOMOD-ready release tree under build/fomod/.
#
# Layout:
#   build/fomod/
#     fomod/ModuleConfig.xml  info.xml  images/<id>.png
#     common/gamedata/...
#     <feature_id>/gamedata/...
#     meta.ini  .mod_id
#
# Wizard: one install step per category; each step is SelectAny checkboxes
# (one per feature). Hover a feature for its description + optional image.
# A Required "About" row per step shows the default hover text.
#
# Local full deploy is still tools/build.sh (all features merged).
# Release features are gated by ROOT/config/features.yml (stage: release).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/src"
STAGE="$ROOT/build/fomod"
BUILD="$ROOT/tools/build.sh"

# shellcheck source=manifest_lib.sh
source "$ROOT/tools/manifest_lib.sh"

HOVER_HINT="Hover each checkbox to see feature information"

xml_escape() {
	sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/"/\&quot;/g'
}

read_trim() {
	local f="$1"
	[[ -f "$f" ]] || { echo ""; return 0; }
	tr -d '\r' < "$f" | sed -e 's/[[:space:]]*$//' | awk 'NR==1{printf "%s",$0; next}{printf "\n%s",$0}'
}

# Category folder -> installer page title (matches MCM labels).
cat_title() {
	case "$1" in
		crafting) echo "Crafting" ;;
		fx) echo "FX" ;;
		hud) echo "HUD" ;;
		items) echo "Items" ;;
		mcm) echo "MCM" ;;
		misc) echo "Misc" ;;
		mutants) echo "Mutants" ;;
		npcs) echo "NPCs" ;;
		pda) echo "PDA" ;;
		player) echo "Player" ;;
		tooltips) echo "Tooltips" ;;
		travel) echo "Travel" ;;
		weapons) echo "Weapons" ;;
		zoom) echo "Zoom" ;;
		*) printf '%s' "$1" | awk '{print toupper(substr($0,1,1)) substr($0,2)}' ;;
	esac
}

# Load ROOT/config/features.yml → FEATURES (stage >= release). common is implicit.
load_manifest() {
	dogma_load_manifest 2 || exit 1
	if [[ "${#FEATURES[@]}" -eq 0 ]]; then
		echo "package-fomod: config/features.yml has no release features (need stage: release)" >&2
		exit 1
	fi
	echo "package-fomod: config/features.yml release (${#FEATURES[@]} features)"
}

manifest_has() {
	local want="$1" f
	for f in "${FEATURES[@]}"; do
		[[ "$f" == "$want" ]] && return 0
	done
	return 1
}

plugin_xml() {
	local name_x="$1" desc_x="$2" image_xml="$3" id="$4" default="$5"
	cat <<EOF

						<plugin name="${name_x}">
							<description>${desc_x}</description>${image_xml}
							<files>
								<folder source="${id}\\gamedata" destination="gamedata" priority="0" />
							</files>
							<typeDescriptor>
								<type name="${default}"/>
							</typeDescriptor>
						</plugin>
EOF
}

about_plugin_xml() {
	local desc_x
	desc_x="$(printf '%s' "$HOVER_HINT" | xml_escape)"
	cat <<EOF

						<plugin name="About">
							<description>${desc_x}</description>
							<typeDescriptor>
								<type name="Required"/>
							</typeDescriptor>
						</plugin>
EOF
}

if [[ ! -f "$BUILD" ]]; then
	echo "package-fomod: missing $BUILD" >&2
	exit 1
fi

rm -rf "$STAGE"
mkdir -p "$STAGE/fomod/images"

VERSION="$(grep -E '^version=' "$ROOT/meta.ini" | head -1 | cut -d= -f2 | tr -d '[:space:]')"
[[ -n "$VERSION" ]] || VERSION="0.0.0"

load_manifest

echo "package-fomod: common"
DOGMA_OUT="$STAGE/common/gamedata" DOGMA_ONLY=common bash "$BUILD"

steps_xml=""
feature_count=0
step_count=0

while IFS= read -r -d '' cat_dir; do
	[[ -d "$cat_dir" ]] || continue
	cat_base="$(basename "$cat_dir")"
	[[ "$cat_base" == "common" ]] && continue
	case "$cat_base" in
		scripts | configs | textures | meshes | anims | sounds | spawns) continue ;;
	esac

	plugins_xml=""
	cat_feat_count=0
	title="$(cat_title "$cat_base")"
	title_x="$(printf '%s' "$title" | xml_escape)"

	while IFS= read -r -d '' feat_dir; do
		[[ -d "$feat_dir" ]] || continue
		feat_base="$(basename "$feat_dir")"
		case "$feat_base" in
			assets | installer) continue ;;
		esac
		rel="${feat_dir#"$SRC"/}"
		rel="${rel//\\/\/}"
		manifest_has "$rel" || continue

		inst="$feat_dir/installer"
		id="$feat_base"
		if [[ -f "$inst/id.txt" ]]; then
			id="$(read_trim "$inst/id.txt")"
		fi
		name="$feat_base"
		if [[ -f "$inst/name.txt" ]]; then
			name="$(read_trim "$inst/name.txt")"
		fi
		desc="D.O.G.M.A. feature: $name"
		if [[ -f "$inst/description.txt" ]]; then
			desc="$(read_trim "$inst/description.txt")"
		fi
		req_blurb=""
		_py=()
		if command -v py >/dev/null 2>&1; then
			_py=(py -3)
		elif command -v python3 >/dev/null 2>&1; then
			_py=(python3)
		elif command -v python >/dev/null 2>&1; then
			_py=(python)
		fi
		if [[ "${#_py[@]}" -gt 0 ]]; then
			req_blurb="$("${_py[@]}" "$ROOT/tools/feature_fomod_requires.py" --feature "$rel" --manifest "$ROOT/config/features.yml" 2>/dev/null || true)"
			if [[ -n "$req_blurb" ]]; then
				desc="${desc}"$'\n\n'"${req_blurb}"
			fi
		fi
		default="Optional"
		if [[ -f "$inst/default.txt" ]]; then
			case "$(read_trim "$inst/default.txt" | tr '[:upper:]' '[:lower:]')" in
				recommended) default="Recommended" ;;
				required) default="Required" ;;
				*) default="Optional" ;;
			esac
		fi

		echo "package-fomod: $rel -> $id"
		DOGMA_OUT="$STAGE/$id/gamedata" DOGMA_ONLY="$rel" bash "$BUILD"

		image_xml=""
		if [[ -f "$inst/image.png" ]]; then
			cp -a "$inst/image.png" "$STAGE/fomod/images/${id}.png"
			image_xml=$'\n\t\t\t\t\t\t\t'"<image path=\"fomod\\images\\${id}.png\" />"
		fi

		name_x="$(printf '%s' "$name" | xml_escape)"
		desc_x="$(printf '%s' "$desc" | xml_escape)"
		plugins_xml+="$(plugin_xml "$name_x" "$desc_x" "$image_xml" "$id" "$default")"
		cat_feat_count=$((cat_feat_count + 1))
		feature_count=$((feature_count + 1))
	done < <(find "$cat_dir" -mindepth 1 -maxdepth 1 -type d -print0 | sort -z)

	[[ "$cat_feat_count" -gt 0 ]] || continue

	# About first so the page opens on the hover hint; features follow.
	plugins_xml="$(about_plugin_xml)${plugins_xml}"

	steps_xml+="$(cat <<EOF

		<installStep name="${title_x}">
			<optionalFileGroups order="Explicit">
				<group name="${title_x}" type="SelectAny">
					<plugins order="Explicit">${plugins_xml}
					</plugins>
				</group>
			</optionalFileGroups>
		</installStep>
EOF
)"
	step_count=$((step_count + 1))
done < <(find "$SRC" -mindepth 1 -maxdepth 1 -type d -print0 | sort -z)

# Final page: recommended third-party mods (info only; nothing installed).
recs_file="$SRC/common/installer/recommendations.txt"
if [[ -f "$recs_file" ]]; then
	recs_plugins=""
	recs_about_desc="Not included in D.O.G.M.A. Install these separately if you want them."
	recs_about_x="$(printf '%s' "$recs_about_desc" | xml_escape)"
	recs_plugins+="$(cat <<EOF

						<plugin name="About">
							<description>${recs_about_x}</description>
							<typeDescriptor>
								<type name="Required"/>
							</typeDescriptor>
						</plugin>
EOF
)"
	rec_count=0
	while IFS= read -r rec_name || [[ -n "$rec_name" ]]; do
		rec_name="$(printf '%s' "$rec_name" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
		[[ -n "$rec_name" ]] || continue
		[[ "$rec_name" == \#* ]] && continue
		name_x="$(printf '%s' "$rec_name" | xml_escape)"
		desc_x="$(printf '%s' "Recommended. Install separately (not part of this package)." | xml_escape)"
		recs_plugins+="$(cat <<EOF

						<plugin name="${name_x}">
							<description>${desc_x}</description>
							<typeDescriptor>
								<type name="Required"/>
							</typeDescriptor>
						</plugin>
EOF
)"
		rec_count=$((rec_count + 1))
	done < "$recs_file"

	if [[ "$rec_count" -gt 0 ]]; then
		steps_xml+="$(cat <<EOF

		<installStep name="Other mods I recommend">
			<optionalFileGroups order="Explicit">
				<group name="Other mods I recommend" type="SelectAny">
					<plugins order="Explicit">${recs_plugins}
					</plugins>
				</group>
			</optionalFileGroups>
		</installStep>
EOF
)"
		step_count=$((step_count + 1))
		echo "package-fomod: recommendations ($rec_count mods)"
	fi
fi

if [[ "$feature_count" -eq 0 ]]; then
	echo "package-fomod: no features found" >&2
	exit 1
fi

cat > "$STAGE/fomod/info.xml" <<EOF
<fomod>
	<Name>D.O.G.M.A.</Name>
	<Author>Dorn</Author>
	<Version>${VERSION}</Version>
	<Website>https://github.com/JoshuaCarter/DOGMA</Website>
	<Description>D.O.G.M.A. — Dorn's Own G.A.M.M.A. Modification Anthology. Pick the features you want.</Description>
	<Groups>
		<element>Miscellaneous</element>
	</Groups>
</fomod>
EOF

cat > "$STAGE/fomod/ModuleConfig.xml" <<EOF
<!-- Generated by tools/package-fomod.sh — do not hand-edit; change src/*/installer/ instead. -->
<config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="http://qconsulting.ca/fo3/ModConfig5.0.xsd">
	<moduleName>D.O.G.M.A.</moduleName>
	<requiredInstallFiles>
		<folder source="common\\gamedata" destination="gamedata" />
		<folder source="common\\mo2" destination="mo2" />
	</requiredInstallFiles>
	<installSteps order="Explicit">${steps_xml}
	</installSteps>
</config>
EOF

cp -a "$ROOT/meta.ini" "$STAGE/meta.ini"
if [[ -f "$ROOT/.mod_id" ]]; then
	cp -a "$ROOT/.mod_id" "$STAGE/.mod_id"
fi
if [[ -f "$ROOT/INFO.md" ]]; then
	cp -a "$ROOT/INFO.md" "$STAGE/INFO.md"
fi
if [[ -f "$ROOT/config/features.yml" ]]; then
	cp -a "$ROOT/config/features.yml" "$STAGE/features.yml"
fi
for _cat in mods.yml suggestions.yml; do
	if [[ -f "$ROOT/config/$_cat" ]]; then
		cp -a "$ROOT/config/$_cat" "$STAGE/$_cat"
	fi
done

echo "package-fomod: done ($feature_count features, $step_count category pages) -> ${STAGE#"$ROOT"/}"

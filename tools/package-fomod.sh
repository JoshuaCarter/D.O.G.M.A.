#!/usr/bin/env bash
# Build a FOMOD-ready release tree under build/fomod/.
#
# Layout:
#   build/fomod/
#     fomod/ModuleConfig.xml  info.xml  images/<id>.png
#     common/gamedata/...
#     common/mo2/...          (tools + packages/<path_key>.zip)
#     <feature_id>/gamedata/...
#     meta.ini  .mod_id
#
# FOMOD: one install step per category; each step is SelectAny checkboxes
# (one per feature). Category pages and plugins follow manifest FEATURES order
# (first appearance of each category; features within a category keep YAML order).
# Hover a feature for its description + optional image.
# A Required "About" row per step shows the default hover text.
#
# Plugin name / description / module id come from config/manifest-*.yml
# (YAML key, desc:, path → id). Optional hover image: src/.../installer/image.png.
#
# Each release feature is also zipped to common/mo2/packages/<path_key>.zip
# (path_key = category_feature, or feature for top-level paths) so DOGMA Setup
# can unpack features locally.
#
# Local full deploy is still tools/build.sh (all features merged).
# Release path mods are gated by ROOT/config/manifest-dogma-*.yml (stage: release).
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

dogma_py() {
	if command -v py >/dev/null 2>&1; then
		py -3 "$@"
	elif command -v python3 >/dev/null 2>&1; then
		python3 "$@"
	elif command -v python >/dev/null 2>&1; then
		python "$@"
	else
		echo "package-fomod: Python required" >&2
		exit 1
	fi
}

# Category folder -> installer page title (matches MCM labels).
cat_title() {
	case "$1" in
		input) echo "Input" ;;
		crafting) echo "Crafting" ;;
		game) echo "Game" ;;
		gui) echo "GUI" ;;
		debug) echo "Debug" ;;
		menu) echo "Menu" ;;
		misc) echo "Misc" ;;
		npcs) echo "NPCs" ;;
		weapons) echo "Weapons" ;;
		*) printf '%s' "$1" | awk '{print toupper(substr($0,1,1)) substr($0,2)}' ;;
	esac
}

# Load ROOT/config manifests → FEATURES (stage >= release).
# Path mods only; common is implicit.
load_manifest() {
	dogma_load_manifest 2 || exit 1
	if [[ "${#FEATURES[@]}" -eq 0 ]]; then
		echo "package-fomod: config manifests have no release path mods (need stage: release)" >&2
		exit 1
	fi
	echo "package-fomod: release path mods (${#FEATURES[@]} features)"
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

# Manifest path → folder under src/ (_common / _debug on disk only).
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

# Append one path-mod plugin into plugins_xml; bumps cat_feat_count / feature_count.
# Sets: plugins_xml (caller-owned), cat_feat_count, feature_count
package_one_feature() {
	local rel="$1"
	local feat_dir="$SRC/$(src_feature_dir "$rel")"
	[[ -d "$feat_dir" ]] || {
		echo "package-fomod: missing feature dir $feat_dir" >&2
		exit 1
	}

	local inst="$feat_dir/installer"
	FEATURE_NAME="" FEATURE_DESC="" FEATURE_ID="" FEATURE_DEFAULT=""
	eval "$(dogma_py "$ROOT/tools/feature_fomod_meta.py" --feature "$rel" --manifest "$ROOT/config")"
	local name="$FEATURE_NAME"
	local desc="$FEATURE_DESC"
	local id="$FEATURE_ID"
	local default="$FEATURE_DEFAULT"
	[[ -n "$id" ]] || { echo "package-fomod: missing meta for $rel" >&2; exit 1; }

	local req_blurb
	req_blurb="$(dogma_py "$ROOT/tools/feature_fomod_requires.py" --feature "$rel" --manifest "$ROOT/config" 2>/dev/null || true)"
	# Effect lists (Requires / Disables / …) are already appended by feature_fomod_meta.
	if [[ -n "$req_blurb" ]] && [[ "$desc" != *"Requires:"* ]]; then
		desc="${desc}"$'\n\n'"${req_blurb}"
	fi

	echo "package-fomod: $rel -> $id"
	DOGMA_OUT="$STAGE/$id/gamedata" DOGMA_ONLY="$rel" bash "$BUILD"

	# Path-keyed zip for Setup wizard (always shipped via common/mo2).
	local pkg_dir="$STAGE/common/mo2/packages"
	mkdir -p "$pkg_dir"
	local pkg_zip="$pkg_dir/${id}.zip"
	rm -f "$pkg_zip"
	dogma_py - "$STAGE/$id" "$pkg_zip" <<'PY'
import sys, zipfile
from pathlib import Path
root = Path(sys.argv[1])
out = Path(sys.argv[2])
with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
    for p in sorted(root.rglob("*")):
        if p.is_file():
            zf.write(p, p.relative_to(root).as_posix())
print(f"package-fomod: package {out.name}")
PY

	local image_xml=""
	if [[ -f "$inst/image.png" ]]; then
		cp -a "$inst/image.png" "$STAGE/fomod/images/${id}.png"
		image_xml=$'\n\t\t\t\t\t\t\t'"<image path=\"fomod\\images\\${id}.png\" />"
	fi

	local name_x desc_x
	name_x="$(printf '%s' "$name" | xml_escape)"
	desc_x="$(printf '%s' "$desc" | xml_escape)"
	plugins_xml+="$(plugin_xml "$name_x" "$desc_x" "$image_xml" "$id" "$default")"
	cat_feat_count=$((cat_feat_count + 1))
	feature_count=$((feature_count + 1))
}

steps_xml=""
feature_count=0
step_count=0

# Category = first path segment (tweaks/foo → tweaks; top-level debug → debug).
feature_category() {
	local rel="$1"
	if [[ "$rel" == */* ]]; then
		printf '%s' "${rel%%/*}"
	else
		printf '%s' "$rel"
	fi
}

# Walk FEATURES in manifest order; group into category steps by first appearance.
declare -a CAT_ORDER=()
declare -A CAT_PLUGINS=()
declare -A CAT_COUNTS=()

for rel in "${FEATURES[@]}"; do
	cat_key="$(feature_category "$rel")"
	# Reserved roots never get FOMOD pages (should already be absent from FEATURES).
	[[ "$cat_key" == "common" || "$cat_key" == "debug" ]] && continue
	if [[ -z "${CAT_COUNTS[$cat_key]+x}" ]]; then
		CAT_ORDER+=("$cat_key")
		CAT_COUNTS[$cat_key]=0
		CAT_PLUGINS[$cat_key]=""
	fi
	plugins_xml="${CAT_PLUGINS[$cat_key]}"
	cat_feat_count="${CAT_COUNTS[$cat_key]}"
	package_one_feature "$rel"
	CAT_PLUGINS[$cat_key]="$plugins_xml"
	CAT_COUNTS[$cat_key]="$cat_feat_count"
done

for cat_key in "${CAT_ORDER[@]}"; do
	[[ "${CAT_COUNTS[$cat_key]}" -gt 0 ]] || continue
	title="$(cat_title "$cat_key")"
	title_x="$(printf '%s' "$title" | xml_escape)"
	# About first so the page opens on the hover hint; features follow.
	plugins_xml="$(about_plugin_xml)${CAT_PLUGINS[$cat_key]}"
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
done

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
	<Description>D.O.G.M.A. - Dorn's Own G.A.M.M.A. Modification Anthology. Pick the features you want.</Description>
	<Groups>
		<element>Miscellaneous</element>
	</Groups>
</fomod>
EOF

cat > "$STAGE/fomod/ModuleConfig.xml" <<EOF
<!-- Generated by tools/package-fomod.sh - do not hand-edit; change config/manifest-*.yml (and optional installer/image.png). -->
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
for _cat in manifest.yml manifest-third-party.yml manifest-dogma-features.yml manifest-dogma-tweaks.yml features.yml mods.yml suggestions.yml mcm_config.yml; do
	if [[ -f "$ROOT/config/$_cat" ]]; then
		cp -a "$ROOT/config/$_cat" "$STAGE/$_cat"
	fi
done

echo "package-fomod: done ($feature_count features, $step_count category pages) -> ${STAGE#"$ROOT"/}"

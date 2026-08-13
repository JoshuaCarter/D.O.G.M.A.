#!/usr/bin/env bash
# Stage a FOMOD-ready tree under build/fomod/.
#
# Layout:
#   build/fomod/
#     fomod/ModuleConfig.xml  info.xml  images/*.png
#     common/gamedata/...
#     common/mo2/...          (tools + packages/<path_key>.zip)
#     <feature_id>/gamedata/...
#     meta.ini  .mod_id
#
# Wizard copy and pages come from fomod/ModuleConfig.xml (hand-authored).
# info.xml is written from meta.ini. Images: fomod/images/.
#
# Each manifest-gated path mod is also zipped to common/mo2/packages/<path_key>.zip
# so DOGMA Setup can unpack features locally.
#
# Local full deploy is still tools/build.sh (all features merged).
# Setup packages are gated by ROOT/config/manifest-dogma-*.yml (stage: release).
# FOMOD checkboxes are whatever ModuleConfig.xml lists.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/src"
STAGE="$ROOT/build/fomod"
BUILD="$ROOT/tools/build.sh"
FOMOD_SRC="$ROOT/fomod/ModuleConfig.xml"

# shellcheck source=manifest_lib.sh
source "$ROOT/tools/manifest_lib.sh"

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

xml_escape() {
	sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/"/\&quot;/g'
}

# Load ROOT/config manifests → FEATURES (stage >= release). Path mods only.
load_manifest() {
	dogma_load_manifest 2 || exit 1
	if [[ "${#FEATURES[@]}" -eq 0 ]]; then
		echo "package-fomod: config manifests have no release path mods (need stage: release)" >&2
		exit 1
	fi
	echo "package-fomod: release path mods (${#FEATURES[@]} features)"
}

src_feature_dir() {
	case "$1" in
		common) echo "_common" ;;
		debug) echo "_debug" ;;
		*) echo "$1" ;;
	esac
}

# Plugin folder ids in ModuleConfig.xml → manifest feature paths (one per line).
xml_feature_paths() {
	dogma_py - "$ROOT" "$FOMOD_SRC" <<'PY'
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
xml_path = Path(sys.argv[2])
sys.path.insert(0, str(root / "src" / "_common" / "mo2" / "tools"))
import dogma_mo2_lib as lib

text = xml_path.read_text(encoding="utf-8")
data = lib.load_manifest(root / "config")
by_key = {
    lib.feature_path_key(feat): feat
    for feat, meta in data.features.items()
    if not meta.always_on
}

ids: list[str] = []
seen: set[str] = set()
for m in re.finditer(r"<plugin\b[^>]*>.*?</plugin>", text, re.S):
    fm = re.search(r'source="([^"]+)"', m.group(0))
    if not fm:
        continue
    folder = fm.group(1).replace("/", "\\").split("\\")[0]
    if folder == "common" or folder in seen:
        continue
    seen.add(folder)
    ids.append(folder)

unknown = [i for i in ids if i not in by_key]
if unknown:
    print(
        "package-fomod: ModuleConfig.xml unknown plugin folders: "
        + ", ".join(unknown),
        file=sys.stderr,
    )
    sys.exit(1)
for i in ids:
    print(by_key[i])
PY
}

if [[ ! -f "$BUILD" ]]; then
	echo "package-fomod: missing $BUILD" >&2
	exit 1
fi
if [[ ! -f "$FOMOD_SRC" ]]; then
	echo "package-fomod: missing $FOMOD_SRC" >&2
	exit 1
fi

rm -rf "$STAGE"
mkdir -p "$STAGE/fomod/images"

VERSION="$(grep -E '^version=' "$ROOT/meta.ini" | head -1 | cut -d= -f2 | tr -d '[:space:]')"
[[ -n "$VERSION" ]] || VERSION="0.0.0"
COMMENTS="$(grep -E '^comments=' "$ROOT/meta.ini" | head -1 | cut -d= -f2-)"
[[ -n "$COMMENTS" ]] || COMMENTS="D.O.G.M.A. - Dorn's Own G.A.M.M.A. Modification Anthology."

load_manifest

XML_FEATURES=()
xml_list="$(mktemp)"
set +e
xml_feature_paths >"$xml_list" 2>"$xml_list.err"
xml_rc=$?
set -e
if (( xml_rc != 0 )); then
	if [[ -s "$xml_list.err" ]]; then
		cat "$xml_list.err" >&2
	else
		echo "package-fomod: failed to read $FOMOD_SRC (exit $xml_rc)" >&2
	fi
	rm -f "$xml_list" "$xml_list.err"
	exit 1
fi
rm -f "$xml_list.err"
while IFS= read -r rel || [[ -n "$rel" ]]; do
	rel="${rel//$'\r'/}"
	[[ -z "$rel" ]] && continue
	XML_FEATURES+=("$rel")
done <"$xml_list"
rm -f "$xml_list"
if [[ "${#XML_FEATURES[@]}" -eq 0 ]]; then
	echo "package-fomod: ModuleConfig.xml has no plugin folders" >&2
	exit 1
fi
echo "package-fomod: ModuleConfig.xml plugins (${#XML_FEATURES[@]} features)"

cp -a "$FOMOD_SRC" "$STAGE/fomod/ModuleConfig.xml"
if [[ -d "$ROOT/fomod/images" ]]; then
	find "$ROOT/fomod/images" -maxdepth 1 -type f \( -name '*.png' -o -name '*.jpg' \) -exec cp -a {} "$STAGE/fomod/images/" \;
fi

DESC_X="$(printf '%s' "$COMMENTS" | xml_escape)"
VER_X="$(printf '%s' "$VERSION" | xml_escape)"
cat > "$STAGE/fomod/info.xml" <<EOF
<fomod>
	<Name>D.O.G.M.A.</Name>
	<Author>Dorn</Author>
	<Version>${VER_X}</Version>
	<Website>https://github.com/JoshuaCarter/DOGMA</Website>
	<Description>${DESC_X}</Description>
	<Groups>
		<element>Miscellaneous</element>
	</Groups>
</fomod>
EOF

echo "package-fomod: common"
DOGMA_OUT="$STAGE/common/gamedata" DOGMA_ONLY=common bash "$BUILD"

package_one_feature() {
	local rel="$1"
	local feat_dir="$SRC/$(src_feature_dir "$rel")"
	[[ -d "$feat_dir" ]] || {
		echo "package-fomod: missing feature dir $feat_dir" >&2
		exit 1
	}
	local id="${rel//\//_}"
	echo "package-fomod: $rel -> $id"
	DOGMA_OUT="$STAGE/$id/gamedata" DOGMA_ONLY="$rel" bash "$BUILD"

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
}

# Stage every XML plugin folder, plus any release feature Setup still needs zipped.
declare -A SEEN=()
STAGE_LIST=()
for rel in "${XML_FEATURES[@]}" "${FEATURES[@]}"; do
	[[ -n "${SEEN[$rel]+x}" ]] && continue
	SEEN[$rel]=1
	STAGE_LIST+=("$rel")
done

for rel in "${STAGE_LIST[@]}"; do
	package_one_feature "$rel"
done

# XML plugin folders must exist after staging.
for rel in "${XML_FEATURES[@]}"; do
	id="${rel//\//_}"
	if [[ ! -d "$STAGE/$id/gamedata" ]]; then
		echo "package-fomod: ModuleConfig.xml references $id but $STAGE/$id/gamedata was not staged" >&2
		exit 1
	fi
done

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

echo "package-fomod: done (${#STAGE_LIST[@]} features staged, ${#XML_FEATURES[@]} FOMOD plugins) -> ${STAGE#"$ROOT"/}"

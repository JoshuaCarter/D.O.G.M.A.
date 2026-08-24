#!/usr/bin/env bash
# Stage a FOMOD-ready tree under build/fomod/ and zip it to build/DOGMA.zip.
#
# Layout:
#   build/fomod/
#     fomod/ModuleConfig.xml  info.xml  images/*.png
#     common/gamedata/...
#     <feature_id>/gamedata/...
#     meta.ini  .mod_id
#
# Wizard: config/fomod.yml + manifest.yml (beta/gold) → tools/gen_fomod.py → ModuleConfig.xml
# info.xml is written from meta.ini.
# Feature hover images: src/<path>/fomod/image.png
# Local full deploy is still tools/build.sh (stage >= local).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/src"
STAGE="$ROOT/build/fomod"
ZIP_OUT="$ROOT/build/DOGMA.zip"
BUILD="$ROOT/tools/build.sh"
GEN="$ROOT/tools/gen_fomod.py"
MANIFEST="$ROOT/config/manifest.yml"
FOMOD_YML="$ROOT/config/fomod.yml"

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

src_feature_dir() {
	case "$1" in
		common) echo "_common" ;;
		debug) echo "_debug" ;;
		*) echo "$1" ;;
	esac
}

if [[ ! -f "$BUILD" ]]; then
	echo "package-fomod: missing $BUILD" >&2
	exit 1
fi
if [[ ! -f "$MANIFEST" ]]; then
	echo "package-fomod: missing $MANIFEST" >&2
	exit 1
fi
if [[ ! -f "$FOMOD_YML" ]]; then
	echo "package-fomod: missing $FOMOD_YML" >&2
	exit 1
fi
if [[ ! -f "$GEN" ]]; then
	echo "package-fomod: missing $GEN" >&2
	exit 1
fi

rm -rf "$STAGE"
mkdir -p "$STAGE/fomod/images"

VERSION="$(grep -E '^version=' "$ROOT/meta.ini" | head -1 | cut -d= -f2 | tr -d '[:space:]')"
[[ -n "$VERSION" ]] || VERSION="0.0.0"
COMMENTS="$(grep -E '^comments=' "$ROOT/meta.ini" | head -1 | cut -d= -f2-)"
[[ -n "$COMMENTS" ]] || COMMENTS="D.O.G.M.A. - Dorn's Own G.A.M.M.A. Modification Anthology."

XML_FEATURES=()
xml_list="$(mktemp)"
set +e
dogma_py "$GEN" --xml "$STAGE/fomod/ModuleConfig.xml" --images-out "$STAGE/fomod/images" >"$xml_list" 2>"$xml_list.err"
xml_rc=$?
set -e
if (( xml_rc != 0 )); then
	if [[ -s "$xml_list.err" ]]; then
		cat "$xml_list.err" >&2
	else
		echo "package-fomod: gen_fomod failed (exit $xml_rc)" >&2
	fi
	rm -f "$xml_list" "$xml_list.err"
	exit 1
fi
if [[ -s "$xml_list.err" ]]; then
	cat "$xml_list.err" >&2
fi
rm -f "$xml_list.err"
while IFS= read -r rel || [[ -n "$rel" ]]; do
	rel="${rel//$'\r'/}"
	[[ -z "$rel" ]] && continue
	XML_FEATURES+=("$rel")
done <"$xml_list"
rm -f "$xml_list"
if [[ "${#XML_FEATURES[@]}" -eq 0 ]]; then
	echo "package-fomod: manifest.yml has no beta/gold plugins" >&2
	exit 1
fi
echo "package-fomod: manifest.yml plugins (${#XML_FEATURES[@]} features)"

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
}

for rel in "${XML_FEATURES[@]}"; do
	package_one_feature "$rel"
done

for rel in "${XML_FEATURES[@]}"; do
	id="${rel//\//_}"
	if [[ ! -d "$STAGE/$id/gamedata" ]]; then
		echo "package-fomod: manifest.yml references $id but $STAGE/$id/gamedata was not staged" >&2
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

echo "package-fomod: zipping"
dogma_py - "$STAGE" "$ZIP_OUT" <<'PY'
import sys
import zipfile
from pathlib import Path

stage = Path(sys.argv[1])
out = Path(sys.argv[2])
out.parent.mkdir(parents=True, exist_ok=True)
if out.exists():
	out.unlink()
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
	for path in sorted(stage.rglob("*")):
		if path.is_file():
			zf.write(path, path.relative_to(stage).as_posix())
PY

echo "package-fomod: done (${#XML_FEATURES[@]} FOMOD plugins) -> ${STAGE#"$ROOT"/} ${ZIP_OUT#"$ROOT"/}"

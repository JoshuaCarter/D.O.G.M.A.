"""Headless render + texture audit for the UI editor.

  py -3 dev/sage/render_preview.py [xml] [out.png]
"""

from __future__ import annotations

import sys
from pathlib import Path

_DEV = Path(__file__).resolve().parent.parent
if str(_DEV) not in sys.path:
    sys.path.insert(0, str(_DEV))

from PyQt6.QtWidgets import QApplication

from sage.model import default_layer_visible, layer_sections
from sage.settings import REPO_ROOT, load_settings
from sage.textures import TextureResolver
from sage.xml_io import UiXmlDocument
from sage.canvas import UiScene


def _make_resolver() -> TextureResolver:
    s = load_settings()
    return TextureResolver(
        texture_scan_roots=[Path(p) for p in s.get("texture_roots", []) if p],
        gamedata_texture_roots=[Path(p) for p in s.get("gamedata_texture_roots", []) if p],
        descr_scan_roots=[Path(p) for p in s.get("textures_descr_roots", []) if p],
        gamedata_descr_roots=[Path(p) for p in s.get("gamedata_descr_roots", []) if p],
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    xml_path = Path(
        argv[0]
        if argv
        else REPO_ROOT
        / "src/menu/loadout_layout/configs/ui/ui_mm_faction_select_16.xml"
    )
    out_path = Path(
        argv[1]
        if len(argv) > 1
        else REPO_ROOT / "build" / "sage_preview.png"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    app = QApplication([])
    settings = load_settings()
    resolver = _make_resolver()
    print("texture roots:", resolver.gamedata_texture_roots)
    print("descr roots:", resolver.gamedata_descr_roots)
    print(f"atlas={len(resolver._atlas)} dds={len(resolver._dds_index)}")

    doc = UiXmlDocument().load(xml_path)
    scene = UiScene(
        resolver,
        label_font_size=int(settings.get("label_font_size", 8)),
        show_element_labels=bool(settings.get("show_element_labels", True)),
        show_box_border=bool(settings.get("show_box_border", False)),
        show_box_fill=bool(settings.get("show_box_fill", False)),
    )
    scene.set_document(doc)

    # Apply default layer visibility (hide popups/options) in one pass
    scene.set_layer_states(
        {
            section.path: default_layer_visible(section)
            for section in layer_sections(doc)
            if section.path
        }
    )

    # Audit textures on visible drawable nodes
    ok = 0
    missing: list[str] = []
    for node in doc.iter_drawables():
        if not node.visible or not node.texture or not node.texture.name:
            continue
        r = resolver.resolve_ref(node.texture)
        if r.image is not None:
            ok += 1
        else:
            missing.append(f"{node.path}: {node.texture.name} → {r.error}")

    print(f"textured_ok={ok} missing={len(missing)}")
    for line in missing[:40]:
        print("  MISS", line)
    if len(missing) > 40:
        print(f"  ... +{len(missing) - 40} more")

    image = scene.render_to_image(scale=1.0)
    image.save(str(out_path))
    print(f"wrote {out_path} ({image.width()}x{image.height()})")
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())

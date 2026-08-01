"""Headless smoke checks for the UI editor model/texture stack."""

from __future__ import annotations

import sys
from pathlib import Path

_DEV = Path(__file__).resolve().parent.parent
if str(_DEV) not in sys.path:
    sys.path.insert(0, str(_DEV))

from sage.settings import REPO_ROOT, load_settings
from sage.textures import TextureResolver
from sage.xml_io import UiXmlDocument


def _resolver() -> TextureResolver:
    s = load_settings()
    return TextureResolver(
        texture_scan_roots=[Path(p) for p in s.get("texture_roots", []) if p],
        gamedata_texture_roots=[Path(p) for p in s.get("gamedata_texture_roots", []) if p],
        descr_scan_roots=[Path(p) for p in s.get("textures_descr_roots", []) if p],
        gamedata_descr_roots=[Path(p) for p in s.get("gamedata_descr_roots", []) if p],
    )


def main() -> int:
    faction = (
        REPO_ROOT
        / "src/tweaks/bigger_new_game_loadout_panel/configs/ui/ui_mm_faction_select.xml"
    )
    tooltip = (
        REPO_ROOT / "src/gui/weapon_tooltips/configs/ui/ui_dogma_gui_weapon_tooltips.xml"
    )
    dots_xml = (
        REPO_ROOT
        / "src/gameplay/true_fast_travel/configs/ui/map_spots_dogma_true_fast_travel.xml"
    )

    resolver = _resolver()
    # Resolve only the atlas ids / DDS this smoke check needs (no full index).
    resolver._ingest_descr_for_ids(
        {"ui_dogma_path_dot_green", "ui_inGame2_button", "ui_new_game_main"}
    )
    print(f"atlas ids: {resolver.atlas_count}")
    assert "ui_dogma_path_dot_green" in resolver._atlas
    assert resolver.find_dds("ui\\dots") is not None
    assert resolver.lookup_atlas("ui_inGame2_button") is not None
    assert resolver.lookup_atlas("ui_new_game_main") is not None
    print(f"dds files: {resolver.dds_count}")

    doc = UiXmlDocument()
    root = doc.load(faction)
    resolver.warm_for_document(root)
    drawables = root.iter_drawables()
    print(f"faction_select drawables: {len(drawables)}")
    assert len(drawables) > 20

    bg = root.find_by_path("background")
    assert bg is not None and bg.is_drawable
    assert bg.width == 1024 and bg.height == 768

    auto = root.find_by_path("background/auto_static")
    assert auto is not None and auto.texture is not None
    assert auto.texture.is_path
    assert auto.texture.has_uv
    bg_tex = resolver.resolve_ref(auto.texture)
    assert bg_tex.image is not None, bg_tex.error

    # Script parents siblings to frame_back
    front = root.find_by_path("main_dialog/frame_front")
    assert front is not None
    assert front.abs_x == 150 and front.abs_y == 72

    btn = root.find_by_path("main_dialog/btn_back")
    assert btn is not None
    assert btn.abs_x == 339 and btn.abs_y == 663
    assert btn.texture is not None
    btn_tex = resolver.resolve_ref(btn.texture)
    assert btn_tex.image is not None, btn_tex.error
    assert btn_tex.atlas_id == "ui_inGame2_button_e"

    # Unsaved meta: options should inherit scroll_options x/y (script AddWindow target)
    from sage.model import apply_meta_positions, build_tree
    import xml.etree.ElementTree as ET

    bare = build_tree(ET.fromstring(faction.read_text(encoding="utf-8")))
    apply_meta_positions(bare, {})
    opts = bare.find_by_path("main_dialog/options")
    scroll = bare.find_by_path("main_dialog/scroll_options")
    assert opts is not None and opts.from_meta
    assert scroll is not None
    assert (opts.x, opts.y) == (scroll.x, scroll.y), ((opts.x, opts.y), (scroll.x, scroll.y))
    popup = bare.find_by_path("main_dialog/popup_faction")
    assert popup is not None and popup.from_meta and (popup.x, popup.y) == (0.0, 0.0)

    tip = doc.load(tooltip)
    panel = tip.find_by_path("panel")
    assert panel is not None
    title = tip.find_by_path("panel/header/title")
    assert title is not None
    assert title.abs_x == panel.x
    assert title.abs_y == panel.y
    print(f"tooltip title abs=({title.abs_x},{title.abs_y}) size={title.width}x{title.height}")

    spots_doc = doc.load(dots_xml)
    green = None
    for n in spots_doc.iter_drawables():
        if n.texture and n.texture.name == "ui_dogma_path_dot_green":
            green = n
            break
    assert green is not None
    resolved = resolver.resolve_ref(green.texture)
    print(f"path_dot_green: {resolved.error or resolved.path}")
    assert resolved.image is not None
    assert resolved.image.size == (32, 32)

    doc.load(faction)
    btn = doc.doc.find_by_path("main_dialog/btn_back")
    assert btn is not None
    old = (btn.x, btn.y)
    btn.set_geometry(x=old[0] + 1, y=old[1] + 1)
    assert btn.element.get("x") == str(int(old[0] + 1))
    btn.set_geometry(x=old[0], y=old[1])

    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

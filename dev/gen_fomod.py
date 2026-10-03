#!/usr/bin/env python3
"""Generate FOMOD ModuleConfig.xml from manifest.yml."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from manifest import (
    DEFAULT_RECOMMENDED,
    FOMOD_STAGES,
    feature_path_key,
    fomod_wizard,
    iter_feature_info,
    src_feature_dir,
)

IMAGE_EXTS = (".png", ".jpg")
FOMOD_IMAGES = ROOT / "fomod" / "images"


def esc_text(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def esc_attr(s: str) -> str:
    return esc_text(s).replace('"', "&quot;")


def win_path(*parts: str) -> str:
    return "\\".join(parts)


def feature_image(feat: str) -> Path | None:
    stem = feature_path_key(feat)
    for ext in IMAGE_EXTS:
        p = FOMOD_IMAGES / f"{stem}{ext}"
        if p.is_file():
            return p
    return None


SKIP_MODROOT_PY = frozenset({"dogma_modlist_delta.py", "stamp_ogg_comments.py"})


def modroot_names(src: Path, feat: str) -> list[str]:
    d = src / src_feature_dir(feat)
    names: list[str] = []
    if (d / "disables.txt").is_file():
        names.append("disables.txt")
    for p in sorted(d.glob("*.py")):
        if p.is_file() and p.name not in SKIP_MODROOT_PY:
            names.append(p.name)
    return names


# Keep in sync with script_dest_basename in dev/build.sh.
def script_dest_basename(path_key: str, src_base: str) -> str:
    stem = src_base[:-7] if src_base.endswith(".script") else src_base
    if stem.startswith("zzzz_"):
        stem = stem[5:]
    if stem == "mcm":
        return f"dogma_{path_key}_mcm.script"
    if stem.startswith("modxml_"):
        return f"modxml_dogma_{path_key}_{stem[7:]}.script"
    if stem == "ammo_craft":
        return f"zzzzzzzzzz_dogma_{path_key}_{stem}.script"
    return f"zzzz_dogma_{path_key}_{stem}.script"


GAMEDATA_ROOTS = frozenset({
    "scripts", "configs", "textures", "meshes", "anims", "sounds",
    "spawns", "materials", "shaders", "particles",
})


def detect_preset_ids(wizard: dict) -> list[str]:
    ids: list[str] = []
    for preset in wizard["presets"]:
        detect = preset.get("detect")
        if detect is None or detect is False:
            continue
        if str(detect).strip().lower() != "files":
            raise ValueError(f"preset {preset.get('id')!r}: detect must be files")
        pid = str(preset.get("id") or "")
        if not pid:
            raise ValueError("detect preset needs id")
        ids.append(pid)
    return ids


def feature_sentinel(src: Path, feat: str) -> str | None:
    """Installed path that exists only when this feature was selected.

    MO2 fileDependency is relative to the game root (parent of gamedata).
    """
    key = feature_path_key(feat)
    root = src / src_feature_dir(feat)
    scripts = root / "scripts"
    names: list[str] = []
    if scripts.is_dir():
        for p in scripts.rglob("*"):
            if not p.is_file() or p.suffix != ".script":
                continue
            rel = p.relative_to(scripts).as_posix()
            if rel == "override" or rel.startswith("override/"):
                continue
            names.append(p.name)
    if names:
        base = "mcm.script" if "mcm.script" in names else sorted(names)[0]
        return win_path("gamedata", "scripts", script_dest_basename(key, base))
    needle = f"dogma_{key}"
    hits: list[str] = []
    if root.is_dir():
        for p in root.rglob("*"):
            if not p.is_file() or needle not in p.name.lower():
                continue
            rel = p.relative_to(root).as_posix()
            bucket = rel.split("/", 1)[0]
            if bucket not in GAMEDATA_ROOTS:
                continue
            hits.append(rel)
    if not hits:
        return None
    return win_path("gamedata", *sorted(hits)[0].split("/"))


def _self_check() -> None:
    assert script_dest_basename("game_ads_zoom", "mcm.script") == "dogma_game_ads_zoom_mcm.script"
    assert script_dest_basename("game_ads_zoom", "main.script") == "zzzz_dogma_game_ads_zoom_main.script"
    assert script_dest_basename("game_x", "modxml_custom_msgs.script") == "modxml_dogma_game_x_custom_msgs.script"
    got = feature_sentinel(ROOT / "src", "game/ads_zoom")
    assert got == win_path("gamedata", "scripts", "dogma_game_ads_zoom_mcm.script"), got


def plugin_entries(features: list[dict], wizard: dict, src: Path) -> list[dict]:
    preset_ids = {str(p.get("id") or "") for p in wizard["presets"]}
    match_ids = set(detect_preset_ids(wizard))
    out: list[dict] = []
    for info in features:
        if info["stage"] not in FOMOD_STAGES:
            continue
        rec = info["recommended"]
        if rec is None:
            rec = list(DEFAULT_RECOMMENDED.get(info["stage"], []))
        unknown = [x for x in rec if x not in preset_ids]
        if unknown:
            raise ValueError(f"{info['path']}: unknown recommended presets {unknown}")
        flagged = [x for x in rec if x in match_ids]
        if flagged:
            raise ValueError(f"{info['path']}: {flagged} detects installed files, not a recommended flag")
        sentinel = feature_sentinel(src, info["path"]) if match_ids else None
        if match_ids and not sentinel:
            raise ValueError(f"{info['path']}: no unique installed file for detect:files")
        # Page already titles the step. Same name on the group = double header in MO2.
        out.append({**info, "group": "", "recommended": rec, "sentinel": sentinel})
    if not out:
        raise ValueError("manifest.yml has no beta/gold plugins")
    return out


def type_descriptor_xml(recommended: list[str], match_ids: list[str], sentinel: str | None, indent: str) -> list[str]:
    t = indent + "\t"
    if not recommended and not (match_ids and sentinel):
        return [f"{indent}<typeDescriptor>", f"{t}<type name=\"Optional\"/>", f"{indent}</typeDescriptor>"]
    lines = [
        f"{indent}<typeDescriptor>",
        f"{t}<dependencyType>",
        f"{t}\t<defaultType name=\"Optional\"/>",
        f"{t}\t<patterns>",
    ]
    pat = t + "\t\t"
    inner = pat + "\t"
    for flag in recommended:
        lines.extend(
            [
                f"{pat}<pattern>",
                f"{inner}<dependencies>",
                f"{inner}\t<flagDependency flag=\"preset\" value=\"{esc_attr(flag)}\"/>",
                f"{inner}</dependencies>",
                f"{inner}<type name=\"Recommended\"/>",
                f"{pat}</pattern>",
            ]
        )
    if sentinel:
        for flag in match_ids:
            for state in ("Active", "Inactive"):
                lines.extend(
                    [
                        f"{pat}<pattern>",
                        f"{inner}<dependencies>",
                        f"{inner}\t<flagDependency flag=\"preset\" value=\"{esc_attr(flag)}\"/>",
                        f"{inner}\t<fileDependency file=\"{esc_attr(sentinel)}\" state=\"{state}\"/>",
                        f"{inner}</dependencies>",
                        f"{inner}<type name=\"Recommended\"/>",
                        f"{pat}</pattern>",
                    ]
                )
    lines.extend([f"{t}\t</patterns>", f"{t}</dependencyType>", f"{indent}</typeDescriptor>"])
    return lines


def plugin_label(feat: dict) -> str:
    name = feat["title"]
    if feat["stage"] == "beta":
        return f"{name} [BETA]"
    return name


INTRO_FLAG = "dogma_intro"
INTRO_OK = "ok"


def parse_intro(wizard: dict) -> dict | None:
    raw = wizard.get("intro")
    if raw is None or raw is False:
        return None
    if isinstance(raw, str):
        desc, page, name = raw, "Welcome", "Continue"
    elif isinstance(raw, dict):
        desc = str(raw.get("desc") or raw.get("text") or "")
        page = str(raw.get("page") or "Welcome").strip() or "Welcome"
        name = str(raw.get("name") or "Continue").strip() or "Continue"
    else:
        raise ValueError("fomod intro must be a string or mapping")
    desc = desc.strip()
    if not desc:
        raise ValueError("fomod intro needs desc")
    return {"page": page, "name": name, "desc": desc}


def intro_visible_xml() -> list[str]:
    return [
        "\t\t\t<visible>",
        f'\t\t\t\t<flagDependency flag="{INTRO_FLAG}" value="{INTRO_OK}"/>',
        "\t\t\t</visible>",
    ]


def intro_plugin_xml(name: str, desc: str) -> list[str]:
    return [
        f'\t\t\t\t\t\t<plugin name="{esc_attr(name)}">',
        f"\t\t\t\t\t\t\t<description>{esc_text(desc)}</description>",
        "\t\t\t\t\t\t\t<conditionFlags>",
        f'\t\t\t\t\t\t\t\t<flag name="{INTRO_FLAG}">{INTRO_OK}</flag>',
        "\t\t\t\t\t\t\t</conditionFlags>",
        "\t\t\t\t\t\t\t<typeDescriptor>",
        '\t\t\t\t\t\t\t\t<type name="Recommended"/>',
        "\t\t\t\t\t\t\t</typeDescriptor>",
        "\t\t\t\t\t\t</plugin>",
    ]


def intro_step_xml(wizard: dict) -> list[str]:
    intro = parse_intro(wizard)
    if not intro:
        return []
    # One plugin + SelectExactlyOne -> MO2 SelectAll (checked, grey).
    # Later pages hidden until dogma_intro=ok.
    lines = [
        f'\t\t<installStep name="{esc_attr(intro["page"])}">',
        '\t\t\t<optionalFileGroups order="Explicit">',
        '\t\t\t\t<group name="" type="SelectExactlyOne">',
        '\t\t\t\t\t<plugins order="Explicit">',
    ]
    lines.extend(intro_plugin_xml(intro["name"], intro["desc"]))
    lines.extend(
        [
            "\t\t\t\t\t</plugins>",
            "\t\t\t\t</group>",
            "\t\t\t</optionalFileGroups>",
            "\t\t</installStep>",
        ]
    )
    return lines


def render_xml(wizard: dict, src: Path, plugins: list[dict]) -> str:
    match_ids = detect_preset_ids(wizard)
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="http://qconsulting.ca/fo3/ModConfig5.0.xsd">',
        f"\t<moduleName>{esc_text(str(wizard['name']))}</moduleName>",
        "\t<requiredInstallFiles>",
    ]
    for req in wizard["required"]:
        source = str(req.get("source") or "").replace("/", "\\")
        dest = str(req.get("dest") or "").replace("/", "\\")
        if not source or not dest:
            raise ValueError(f"required entry needs source and dest: {req!r}")
        lines.append(f'\t\t<folder source="{esc_attr(source)}" destination="{esc_attr(dest)}" />')
    for name in modroot_names(src, "common"):
        lines.append(
            f'\t\t<file source="{esc_attr(name)}" destination="{esc_attr(name)}" />'
        )
    lines.extend(["\t</requiredInstallFiles>", '\t<installSteps order="Explicit">'])
    lines.extend(intro_step_xml(wizard))
    gate = intro_visible_xml() if parse_intro(wizard) else []

    lines.extend(
        [
            '\t\t<installStep name="Preset">',
            *gate,
            '\t\t\t<optionalFileGroups order="Explicit">',
            '\t\t\t\t<group name="Install preset" type="SelectExactlyOne">',
            '\t\t\t\t\t<plugins order="Explicit">',
        ]
    )
    for preset in wizard["presets"]:
        pid = str(preset.get("id") or "")
        pname = str(preset.get("name") or pid)
        pdesc = str(preset.get("desc") or "")
        ptype = str(preset.get("type") or "Optional")
        if not pid or not pname or not pdesc:
            raise ValueError(f"preset needs id, name, desc: {preset!r}")
        lines.extend(
            [
                f'\t\t\t\t\t\t<plugin name="{esc_attr(pname)}">',
                f"\t\t\t\t\t\t\t<description>{esc_text(pdesc)}</description>",
                "\t\t\t\t\t\t\t<conditionFlags>",
                f'\t\t\t\t\t\t\t\t<flag name="preset">{esc_text(pid)}</flag>',
                "\t\t\t\t\t\t\t</conditionFlags>",
                "\t\t\t\t\t\t\t<typeDescriptor>",
                f'\t\t\t\t\t\t\t\t<type name="{esc_attr(ptype)}"/>',
                "\t\t\t\t\t\t\t</typeDescriptor>",
                "\t\t\t\t\t\t</plugin>",
            ]
        )
    lines.extend(
        [
            "\t\t\t\t\t</plugins>",
            "\t\t\t\t</group>",
            "\t\t\t</optionalFileGroups>",
            "\t\t</installStep>",
        ]
    )

    by_page: dict[str, dict[str, list[dict]]] = {}
    page_order: list[str] = []
    group_order: dict[str, list[str]] = {}
    for feat in plugins:
        if feat["page"] not in by_page:
            page_order.append(feat["page"])
            by_page[feat["page"]] = {}
            group_order[feat["page"]] = []
        groups = by_page[feat["page"]]
        if feat["group"] not in groups:
            group_order[feat["page"]].append(feat["group"])
            groups[feat["group"]] = []
        groups[feat["group"]].append(feat)

    for pname in page_order:
        lines.extend(
            [
                f'\t\t<installStep name="{esc_attr(pname)}">',
                *gate,
                '\t\t\t<optionalFileGroups order="Explicit">',
            ]
        )
        for gname in group_order[pname]:
            items = by_page[pname][gname]
            gtype = "SelectAny"
            lines.extend(
                [
                    f'\t\t\t\t<group name="{esc_attr(gname)}" type="{gtype}">',
                    '\t\t\t\t\t<plugins order="Explicit">',
                ]
            )
            for feat in items:
                folder = feature_path_key(feat["path"])
                desc = feat.get("desc") or feat["title"]
                image = feature_image(feat["path"])
                lines.append(f'\t\t\t\t\t\t<plugin name="{esc_attr(plugin_label(feat))}">')
                lines.append(f"\t\t\t\t\t\t\t<description>{esc_text(desc)}</description>")
                if image:
                    dest_name = f"{folder}{image.suffix.lower()}"
                    lines.append(f'\t\t\t\t\t\t\t<image path="{esc_attr(win_path("fomod", "images", dest_name))}" />')
                file_lines = [
                    "\t\t\t\t\t\t\t<files>",
                    f'\t\t\t\t\t\t\t\t<folder source="{esc_attr(win_path(folder, "gamedata"))}" destination="gamedata" priority="0" />',
                ]
                for name in modroot_names(src, feat["path"]):
                    file_lines.append(
                        f'\t\t\t\t\t\t\t\t<file source="{esc_attr(win_path(folder, name))}" destination="{esc_attr(name)}" />'
                    )
                file_lines.append("\t\t\t\t\t\t\t</files>")
                lines.extend(file_lines)
                lines.extend(type_descriptor_xml(feat["recommended"], match_ids, feat.get("sentinel"), "\t\t\t\t\t\t\t"))
                lines.append("\t\t\t\t\t\t</plugin>")
            lines.extend(["\t\t\t\t\t</plugins>", "\t\t\t\t</group>"])
        lines.extend(["\t\t\t</optionalFileGroups>", "\t\t</installStep>"])

    lines.extend(["\t</installSteps>", "</config>", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate FOMOD ModuleConfig.xml")
    parser.add_argument("--xml", required=True, help="write ModuleConfig.xml here")
    args = parser.parse_args()

    config = ROOT
    try:
        _self_check()
        wizard = fomod_wizard(config)
        src = ROOT / "src"
        plugins = plugin_entries(iter_feature_info(config), wizard, src)
        match_ids = detect_preset_ids(wizard)
        if match_ids:
            print(
                f"gen_fomod: detect {', '.join(match_ids)} ({len(plugins)} file checks)",
                file=sys.stderr,
            )
        xml = render_xml(wizard, src, plugins)
    except (ValueError, RuntimeError) as exc:
        print(f"gen_fomod: {exc}", file=sys.stderr)
        return 1

    out = Path(args.xml)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(xml, encoding="utf-8")

    for p in plugins:
        print(p["path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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


def modroot_names(src: Path, feat: str) -> list[str]:
    d = src / src_feature_dir(feat)
    names: list[str] = []
    if (d / "disables.txt").is_file():
        names.append("disables.txt")
    for p in sorted(d.glob("*.py")):
        if p.is_file():
            names.append(p.name)
    return names


def plugin_entries(features: list[dict], wizard: dict) -> list[dict]:
    preset_ids = {str(p.get("id") or "") for p in wizard["presets"]}
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
        out.append({**info, "group": info["page"], "recommended": rec})
    if not out:
        raise ValueError("manifest.yml has no beta/gold plugins")
    return out


def type_descriptor_xml(recommended: list[str], indent: str) -> list[str]:
    t = indent + "\t"
    if not recommended:
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
        refuse = "I am blind"
    elif isinstance(raw, dict):
        desc = str(raw.get("desc") or raw.get("text") or "")
        page = str(raw.get("page") or "Welcome").strip() or "Welcome"
        name = str(raw.get("name") or "Continue").strip() or "Continue"
        refuse = str(raw.get("refuse") or "I have not read this").strip() or "I have not read this"
    else:
        raise ValueError("fomod intro must be a string or mapping")
    desc = desc.strip()
    if not desc:
        raise ValueError("fomod intro needs desc")
    if refuse == name:
        raise ValueError("fomod intro refuse must differ from name")
    return {"page": page, "name": name, "refuse": refuse, "desc": desc}


def intro_visible_xml() -> list[str]:
    return [
        "\t\t\t<visible>",
        f'\t\t\t\t<flagDependency flag="{INTRO_FLAG}" value="{INTRO_OK}"/>',
        "\t\t\t</visible>",
    ]


def intro_plugin_xml(name: str, desc: str, ack: bool) -> list[str]:
    lines = [
        f'\t\t\t\t\t\t<plugin name="{esc_attr(name)}">',
        f"\t\t\t\t\t\t\t<description>{esc_text(desc)}</description>",
    ]
    if ack:
        lines.extend(
            [
                "\t\t\t\t\t\t\t<conditionFlags>",
                f'\t\t\t\t\t\t\t\t<flag name="{INTRO_FLAG}">{INTRO_OK}</flag>',
                "\t\t\t\t\t\t\t</conditionFlags>",
            ]
        )
    lines.extend(
        [
            "\t\t\t\t\t\t\t<typeDescriptor>",
            '\t\t\t\t\t\t\t\t<type name="Optional"/>',
            "\t\t\t\t\t\t\t</typeDescriptor>",
            "\t\t\t\t\t\t</plugin>",
        ]
    )
    return lines


def intro_step_xml(wizard: dict) -> list[str]:
    intro = parse_intro(wizard)
    if not intro:
        return []
    # MO2: one plugin + SelectAtLeastOne/ExactlyOne becomes SelectAll
    # (checked + grey). Two radios, refuse first so they must pick the ack.
    # Later pages are hidden until dogma_intro=ok.
    lines = [
        f'\t\t<installStep name="{esc_attr(intro["page"])}">',
        '\t\t\t<optionalFileGroups order="Explicit">',
        f'\t\t\t\t<group name="{esc_attr(intro["page"])}" type="SelectExactlyOne">',
        '\t\t\t\t\t<plugins order="Explicit">',
    ]
    lines.extend(intro_plugin_xml(intro["refuse"], intro["desc"], ack=False))
    lines.extend(intro_plugin_xml(intro["name"], intro["desc"], ack=True))
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
                lines.extend(type_descriptor_xml(feat["recommended"], "\t\t\t\t\t\t\t"))
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
        wizard = fomod_wizard(config)
        plugins = plugin_entries(iter_feature_info(config), wizard)
        xml = render_xml(wizard, ROOT / "src", plugins)
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

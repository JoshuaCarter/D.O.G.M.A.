#!/usr/bin/env python3
"""Generate FOMOD ModuleConfig.xml from config/fomod.yml + manifest.yml."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from manifest import (
    FOMOD_STAGES,
    feature_path_key,
    fomod_wizard,
    iter_feature_info,
    src_feature_dir,
)

DEFAULT_RECOMMENDED = ["baseline", "all"]
IMAGE_NAMES = ("image.png", "image.jpg")


def esc_text(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def esc_attr(s: str) -> str:
    return esc_text(s).replace('"', "&quot;")


def win_path(*parts: str) -> str:
    return "\\".join(parts)


def feature_fomod_dir(src: Path, feat: str) -> Path:
    return src / src_feature_dir(feat) / "fomod"


def feature_desc(src: Path, feat: str) -> str:
    desc_file = feature_fomod_dir(src, feat) / "desc.txt"
    if desc_file.is_file():
        text = desc_file.read_text(encoding="utf-8").strip()
        if text:
            return text
    raise ValueError(f"no desc for {feat} (need src/{src_feature_dir(feat)}/fomod/desc.txt)")


def feature_image(src: Path, feat: str) -> Path | None:
    d = feature_fomod_dir(src, feat)
    for name in IMAGE_NAMES:
        p = d / name
        if p.is_file():
            return p
    return None


def parse_plugin_item(raw) -> tuple[str, list[str] | None]:
    if isinstance(raw, str):
        feat = raw.strip().replace("\\", "/")
        if not feat:
            raise ValueError("empty plugin path")
        return feat, None
    if not isinstance(raw, dict):
        raise ValueError(f"plugin must be a path or map: {raw!r}")
    feat = str(raw.get("path") or "").strip().replace("\\", "/")
    if not feat:
        raise ValueError(f"plugin needs path: {raw!r}")
    rec = raw.get("recommended") if "recommended" in raw else None
    if rec is not None:
        if not isinstance(rec, list):
            raise ValueError(f"{feat}: recommended must be a list")
        rec = [str(x) for x in rec]
    return feat, rec


def plugin_entries(catalog: dict[str, dict], wizard: dict) -> list[dict]:
    preset_ids = {str(p.get("id") or "") for p in wizard["presets"]}
    out: list[dict] = []
    seen: set[str] = set()
    for page in wizard["pages"]:
        pname = str(page.get("name") or "")
        if not pname:
            raise ValueError("page needs name")
        for group in page.get("groups") or []:
            gname = str(group.get("name") or "")
            if not gname:
                raise ValueError(f"group on page {pname!r} needs name")
            for raw in group.get("plugins") or []:
                feat, rec = parse_plugin_item(raw)
                if feat in seen:
                    raise ValueError(f"duplicate plugin path {feat}")
                seen.add(feat)
                info = catalog.get(feat)
                if not info:
                    raise ValueError(f"fomod.yml unknown path {feat}")
                if info["stage"] not in FOMOD_STAGES:
                    raise ValueError(f"{feat}: listed in fomod.yml but stage is {info['stage']} (need beta|gold)")
                if rec is None:
                    rec = list(DEFAULT_RECOMMENDED)
                unknown = [x for x in rec if x not in preset_ids]
                if unknown:
                    raise ValueError(f"{feat}: unknown recommended presets {unknown}")
                out.append({**info, "page": pname, "group": gname, "recommended": rec})
    if not out:
        raise ValueError("fomod.yml has no plugins")
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
    lines.extend(["\t</requiredInstallFiles>", '\t<installSteps order="Explicit">'])

    lines.extend(
        [
            '\t\t<installStep name="Preset">',
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
                desc = feature_desc(src, feat["path"])
                image = feature_image(src, feat["path"])
                lines.append(f'\t\t\t\t\t\t<plugin name="{esc_attr(plugin_label(feat))}">')
                lines.append(f"\t\t\t\t\t\t\t<description>{esc_text(desc)}</description>")
                if image:
                    dest_name = f"{folder}{image.suffix.lower()}"
                    lines.append(f'\t\t\t\t\t\t\t<image path="{esc_attr(win_path("fomod", "images", dest_name))}" />')
                lines.extend(
                    [
                        "\t\t\t\t\t\t\t<files>",
                        f'\t\t\t\t\t\t\t\t<folder source="{esc_attr(win_path(folder, "gamedata"))}" destination="gamedata" priority="0" />',
                        "\t\t\t\t\t\t\t</files>",
                    ]
                )
                lines.extend(type_descriptor_xml(feat["recommended"], "\t\t\t\t\t\t\t"))
                lines.append("\t\t\t\t\t\t</plugin>")
            lines.extend(["\t\t\t\t\t</plugins>", "\t\t\t\t</group>"])
        lines.extend(["\t\t\t</optionalFileGroups>", "\t\t</installStep>"])

    lines.extend(["\t</installSteps>", "</config>", ""])
    return "\n".join(lines)


def copy_images(src: Path, plugins: list[dict], dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    for p in plugins:
        image = feature_image(src, p["path"])
        if not image:
            continue
        dest = dest_dir / f"{feature_path_key(p['path'])}{image.suffix.lower()}"
        shutil.copy2(image, dest)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate FOMOD ModuleConfig.xml")
    parser.add_argument("--xml", required=True, help="write ModuleConfig.xml here")
    parser.add_argument("--images-out", help="copy feature hover images here")
    args = parser.parse_args()

    config = ROOT / "config"
    try:
        wizard = fomod_wizard(config)
        catalog = {r["path"]: r for r in iter_feature_info(config)}
        plugins = plugin_entries(catalog, wizard)
        xml = render_xml(wizard, ROOT / "src", plugins)
    except (ValueError, RuntimeError) as exc:
        print(f"gen_fomod: {exc}", file=sys.stderr)
        return 1

    out = Path(args.xml)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(xml, encoding="utf-8")

    if args.images_out:
        copy_images(ROOT / "src", plugins, Path(args.images_out))

    for p in plugins:
        print(p["path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

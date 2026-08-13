#!/usr/bin/env python3
"""Snapshot config/mcm_config.yml + appdata/axr_options.ltx → Lua _data.script."""

from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_YAML = _REPO / "config" / "mcm_config.yml"
_AXR = _REPO / "appdata" / "axr_options.ltx"
_OUT = _REPO / "src" / "_common" / "defaults" / "scripts" / "_data.script"

_ASSIGN = re.compile(r"^(\s*)([^=]+?)\s*=\s*(.*?)\s*$")
_AXR_LINE = re.compile(r"^(\S+)\s*=\s*(.*)$")


def _fail(msg: str) -> None:
    print(f"gen_defaults: {msg}", file=sys.stderr)
    raise SystemExit(1)


def _lua_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _parse_scalar(raw: str):
    s = (raw or "").strip()
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if s.startswith("'") and s.endswith("'") and len(s) >= 2:
        s = s[1:-1]
    elif s.startswith('"') and s.endswith('"') and len(s) >= 2:
        s = s[1:-1]
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        return s


def _lua_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int) and not isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        if v.is_integer():
            return str(int(v))
        return repr(v)
    return _lua_quote(str(v))


def _lua_table(pairs: dict) -> str:
    if not pairs:
        return "{}"
    parts = [f"[{_lua_quote(k)}] = {_lua_value(v)}" for k, v in pairs.items()]
    return "{ " + ", ".join(parts) + " }"


def parse_axr_sections(path: Path) -> dict[str, dict[str, object]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    sections: dict[str, dict[str, object]] = {}
    current: str | None = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1].strip().lower()
            sections.setdefault(current, {})
            continue
        if current is None:
            continue
        m = _ASSIGN.match(raw)
        if not m:
            continue
        key = m.group(2).strip()
        if not key:
            continue
        sections[current][key] = _parse_scalar(m.group(3))
    return sections


def _slug(name: str, fallback: str) -> str:
    out: list[str] = []
    for c in (name or fallback).lower():
        if c.isalnum():
            out.append(c)
        elif out and out[-1] != "_":
            out.append("_")
    s = "".join(out).strip("_")
    return s or "pack"


def _prefix_hits(section: dict[str, object], prefix: str) -> dict[str, object]:
    needle = prefix + "/"
    return {k: v for k, v in section.items() if k.startswith(needle)}


def _parse_axr_line(raw: str) -> tuple[str, object]:
    s = raw.strip()
    m = _AXR_LINE.match(s)
    if not m:
        _fail(f"axr: line must be 'key = value' (got {raw!r})")
    return m.group(1), _parse_scalar(m.group(2))


def main() -> int:
    try:
        import yaml
    except ImportError:
        _fail("PyYAML required")

    if not _YAML.is_file():
        _fail(f"missing {_YAML}")
    if not _AXR.is_file():
        _fail(f"missing {_AXR}")

    raw = yaml.safe_load(_YAML.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        _fail(f"{_YAML.name} root must be a mapping")

    axr = parse_axr_sections(_AXR)
    mcm_sec = axr.get("mcm") or {}
    opt_sec = axr.get("options") or {}
    if not mcm_sec and not opt_sec:
        _fail(f"{_AXR.name} has no [mcm] or [options] keys")

    packs: list[dict] = []
    used_ids: set[str] = set()
    for name, body in raw.items():
        heading = str(name).strip()
        if not heading:
            continue
        if not isinstance(body, dict):
            _fail(f"{heading!r}: want key:/commands:/axr: mapping, not {type(body).__name__}")
        key = str(body["key"]).strip() if body.get("key") is not None else ""
        commands = body.get("commands") or []
        axr_lines = body.get("axr") or []
        if commands and not isinstance(commands, list):
            _fail(f"{heading!r}: commands: must be a list")
        if axr_lines and not isinstance(axr_lines, list):
            _fail(f"{heading!r}: axr: must be a list")
        cmds = [str(c) for c in commands]
        mcm: dict[str, object] = {}
        options: dict[str, object] = {}
        if key:
            mcm.update(_prefix_hits(mcm_sec, key))
            options.update(_prefix_hits(opt_sec, key))
            if not mcm and not options and not cmds and not axr_lines:
                _fail(f"{heading!r}: key {key!r} matched no axr keys")
        elif not cmds and not axr_lines:
            _fail(f"{heading!r}: need key:, commands:, or axr:")
        for line in axr_lines:
            ak, av = _parse_axr_line(str(line))
            if ak in mcm_sec:
                mcm[ak] = av
            elif ak in opt_sec:
                options[ak] = av
            else:
                _fail(f"{heading!r}: axr key {ak!r} not in [mcm] or [options]")
        pack_id = _slug(key or heading, heading)
        base = pack_id
        n = 2
        while pack_id in used_ids:
            pack_id = f"{base}_{n}"
            n += 1
        used_ids.add(pack_id)
        packs.append(
            {
                "id": pack_id,
                "name": heading,
                "mcm": mcm,
                "options": options,
                "commands": cmds,
            }
        )

    if not packs:
        _fail("mcm_config.yml has no packs")

    lines = [
        "-- Generated by tools/gen_defaults.py — do not edit.",
        "_G.dogma_defaults_data = {",
    ]
    for pack in packs:
        cmd_lua = ", ".join(_lua_quote(c) for c in pack["commands"])
        lines.append("  {")
        lines.append(f"    id = {_lua_quote(pack['id'])},")
        lines.append(f"    name = {_lua_quote(pack['name'])},")
        lines.append(f"    mcm = {_lua_table(pack['mcm'])},")
        lines.append(f"    options = {_lua_table(pack['options'])},")
        lines.append(f"    commands = {{ {cmd_lua} }}," if cmd_lua else "    commands = {},")
        lines.append("  },")
    lines.append("}")
    lines.append("")
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"gen_defaults: wrote {_OUT.relative_to(_REPO)} ({len(packs)} pack(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

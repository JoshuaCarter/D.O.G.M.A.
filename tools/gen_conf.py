#!/usr/bin/env python3
"""Generate src/**/scripts/_conf.script from each feature's mcm.script defaults.

Also prints the FEATURE / MOD_ID / defaults for wiring. Does not patch main/mcm
(those are edited separately so call-site shape stays readable).
"""
from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

LEAF_TYPES = {"check", "track", "list", "key_bind", "input", "radio", "radio_h", "button"}


def strip_comments(s: str) -> str:
    return re.sub(r"--[^\n]*", "", s)


def find_matching(s: str, open_idx: int) -> int:
    depth = 0
    i = open_idx
    while i < len(s):
        c = s[i]
        if c == '"':
            i += 1
            while i < len(s) and s[i] != '"':
                i += 2 if s[i] == "\\" else 1
            i += 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def unwrap_calls(s: str, fname: str) -> str:
    token = fname + "("
    while True:
        idx = s.find(token)
        if idx < 0:
            break
        paren = idx + len(fname)
        depth = 0
        j = paren
        while j < len(s):
            if s[j] == '"':
                j += 1
                while j < len(s) and s[j] != '"':
                    j += 2 if s[j] == "\\" else 1
                j += 1
                continue
            if s[j] == "(":
                depth += 1
            elif s[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        inner = s[paren + 1 : j].strip()
        s = s[:idx] + inner + s[j + 1 :]
    return s


def parse_lua_value(s: str, i: int):
    while i < len(s) and s[i] in " \t\n\r":
        i += 1
    if i >= len(s):
        return None, i
    if s[i] == "{":
        end = find_matching(s, i)
        return parse_lua_table(s[i : end + 1]), end + 1
    if s[i] == '"':
        m = re.match(r'"([^"]*)"', s[i:])
        return m.group(1), i + m.end()
    if s[i] == "'":
        m = re.match(r"'([^']*)'", s[i:])
        return m.group(1), i + m.end()
    m = re.match(
        r"(-?\d+(?:\.\d+)?|true|false|nil|"
        r"[A-Za-z_][A-Za-z0-9_:]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)",
        s[i:],
    )
    if m:
        return m.group(1), i + m.end()
    return s[i], i + 1


def parse_lua_table(table_src: str):
    assert table_src[0] == "{"
    s = table_src[1:-1]
    items = []
    i = 0
    current: dict = {}

    def flush():
        nonlocal current
        if current:
            items.append(current)
            current = {}

    while i < len(s):
        while i < len(s) and s[i] in " \t\n\r,":
            i += 1
        if i >= len(s):
            break
        if s[i] == "{":
            flush()
            end = find_matching(s, i)
            nested = parse_lua_table(s[i : end + 1])
            items.extend(nested)
            i = end + 1
            continue
        km = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*", s[i:])
        if km:
            key = km.group(1)
            i += km.end()
            val, i = parse_lua_value(s, i)
            current[key] = val
            continue
        flush()
        val, i = parse_lua_value(s, i)
        items.append({"_bare": val})
    flush()
    return items


def expand_helpers(body: str) -> str:
    body = re.sub(
        r'kb_bind\(\s*"([^"]+)"\s*,\s*("[^"]*")\s*\)',
        r'{ id = "\1", type = "key_bind", def = -1, hint = \2 }',
        body,
    )
    body = re.sub(
        r'kb_modifier\(\s*"([^"]+)"\s*\)',
        r'{ id = "\1", type = "radio_h", def = 0 }',
        body,
    )
    body = re.sub(
        r'kb_mode\(\s*"([^"]+)"\s*\)',
        r'{ id = "\1", type = "radio_h", def = 0 }',
        body,
    )
    body = re.sub(
        r'wads_track\(\s*"([^"]+)"\s*,\s*"[^"]*"\s*\)',
        r'{ id = "\1", type = "track", def = 0 }',
        body,
    )

    def repl_cat(m):
        cat = m.group(1)
        return ", ".join(
            '{ id = "wads_%s_%s", type = "track", def = 0 }' % (cat, suf)
            for suf in ("irons", "sight", "scope")
        )

    body = re.sub(r'category_wads\(\s*"([^"]+)"\s*\)', repl_cat, body)
    return body


def extract_defaults(mcm_text: str) -> tuple[str | None, dict[str, str]]:
    body_m = re.search(r"function on_mcm_load\(\)(.*)\nend\s*$", mcm_text, re.S)
    if not body_m:
        return None, {}
    body = strip_comments(body_m.group(1))
    # Inline local ads_gr / etc that are later referenced: pull tables assigned before return
    # free_zoom: ads_gr = { ... } then return { gr = { { id=ads_zoom, gr = ads_gr } } }
    # After expand + unwrap with_header, replace identifier gr = ads_gr with inlined table
    body = expand_helpers(body)
    body = unwrap_calls(body, "MCM.with_header")

    # Capture local NAME = { ... } tables and substitute into return
    locals_map = {}
    for m in re.finditer(r"local\s+(\w+)\s*=\s*\{", body):
        name = m.group(1)
        start = m.end() - 1
        end = find_matching(body, start)
        if end > 0:
            locals_map[name] = body[start : end + 1]

    # free_zoom mutates ads_gr after definition — already expanded category_wads into source
    # before locals capture if we expand_helpers first — good.

    rm = re.search(r"return\s*\{", body)
    if not rm:
        return None, {}
    start = rm.end() - 1
    end = find_matching(body, start)
    ret_src = body[start : end + 1]

    # Replace gr = NAME with gr = {..} for known locals
    for name, table in locals_map.items():
        ret_src = re.sub(rf"\bgr\s*=\s*{name}\b", f"gr = {table}", ret_src)

    root_table = parse_lua_table(ret_src)
    if not root_table:
        return None, {}
    root = root_table[0]
    defaults: dict[str, str] = {}

    def walk(node, path):
        if not isinstance(node, dict):
            return
        nid = node.get("id")
        if nid and not isinstance(nid, str):
            nid = None
        npath = path + ([nid] if nid else [])
        typ = node.get("type")
        if isinstance(typ, str) and typ in LEAF_TYPES and "def" in node:
            if len(npath) >= 2:
                key = "/".join(npath[1:])
            else:
                key = npath[-1] if npath else "?"
            defaults[key] = str(node["def"])
        gr = node.get("gr")
        if isinstance(gr, list):
            for ch in gr:
                walk(ch, npath)

    walk(root, [])
    return root.get("id"), defaults


def lua_literal(val: str) -> str:
    val = val.strip()
    if val in ("true", "false", "nil") or re.fullmatch(r"-?\d+(?:\.\d+)?", val):
        return val
    if (val.startswith('"') and val.endswith('"')) or (
        val.startswith("'") and val.endswith("'")
    ):
        return val
    # bare word like default from bad parse — quote it
    return f'"{val}"'


def format_conf(feature: str, mod_id: str, defaults: dict[str, str]) -> str:
    lines = [
        f"-- {feature} conf: MOD_ID + defaults (MCM def= and CONFIG share these).",
        "",
        f"_G.dogma_{feature}_conf = {{",
        f'\tMOD_ID = "{mod_id}",',
        "\tdefaults = {",
    ]
    for key in sorted(defaults):
        lines.append(f'\t\t["{key}"] = {lua_literal(defaults[key])},')
    lines += [
        "\t},",
        "}",
        "",
    ]
    return "\n".join(lines)


def main():
    for mcm_path in sorted(SRC.rglob("mcm.script")):
        feat_dir = mcm_path.parent.parent
        text = mcm_path.read_text(encoding="utf-8")
        feature, defaults = extract_defaults(text)
        if not feature:
            print("SKIP (no tree)", feat_dir)
            continue
        # Also pull MOD_ID from existing main if present
        mod_id = f"dogma/{feature}"
        for sp in (feat_dir / "scripts").glob("*.script"):
            if sp.name in ("mcm.script", "_conf.script"):
                continue
            m = re.search(r'local MOD_ID = "([^"]+)"', sp.read_text(encoding="utf-8"))
            if m:
                mod_id = m.group(1)
                break
        conf_path = feat_dir / "scripts" / "_conf.script"
        conf_path.write_text(format_conf(feature, mod_id, defaults), encoding="utf-8", newline="\n")
        print(f"wrote {conf_path.relative_to(ROOT)} ({len(defaults)} defaults) MOD={mod_id}")


if __name__ == "__main__":
    main()

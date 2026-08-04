"""Rebuild faction item_ltx_overrides from the SALE diagnostics log.

Replays every `item ltx toggle` line in chronological order. The `paint=` field
records the post-toggle (faction, baseline) pair, which maps 1:1 back onto the
stored override value:

    paint=faction  -> faction on,  baseline off -> "include"
    paint=baseline -> faction off, baseline on  -> "exclude"
    paint=both     -> faction on,  baseline on  -> inherit (no entry)
    paint=off      -> faction off, baseline off -> inherit (no entry)
"""

from __future__ import annotations

import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
LOG = HERE / "sale.log"
BALANCE = HERE / "balance.yml"

LINE = re.compile(
    r"item ltx toggle sec=(?P<sec>\S+) fac=(?P<fac>\S+) "
    r"was_in=(?P<was>\S+) in_ltx=(?P<now>\S+) paint=(?P<paint>\S+)"
)
PAINT_TO_OVERRIDE = {
    "faction": "include",
    "baseline": "exclude",
    "both": None,
    "off": None,
}


def replay(log_text: str) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    baseline: dict[str, str] = {}
    factions: dict[str, dict[str, str]] = {}
    for m in LINE.finditer(log_text):
        sec, fac, paint = m["sec"], m["fac"], m["paint"]
        if fac == "Default":
            if m["now"] == "True":
                baseline[sec] = "include"
            else:
                baseline.pop(sec, None)
            continue
        fmap = factions.setdefault(fac, {})
        value = PAINT_TO_OVERRIDE.get(paint)
        if value is None:
            fmap.pop(sec, None)
        else:
            fmap[sec] = value
    # An exclude only means something when the baseline includes the section.
    for fmap in factions.values():
        for sec in [s for s, v in fmap.items() if v == "exclude" and s not in baseline]:
            fmap.pop(sec)
    return baseline, {f: m for f, m in factions.items() if m}


def main(apply: bool) -> int:
    baseline, factions = replay(LOG.read_text(encoding="utf-8", errors="replace"))
    data = yaml.safe_load(BALANCE.read_text(encoding="utf-8")) or {}
    cur_fac = data.get("factions") or {}

    print(f"replayed baseline entries: {len(baseline)}")
    for fac in sorted(factions):
        inc = sum(1 for v in factions[fac].values() if v == "include")
        exc = sum(1 for v in factions[fac].values() if v == "exclude")
        have = len((cur_fac.get(fac) or {}).get("item_ltx_overrides") or {})
        print(f"  {fac:<10} include={inc:<3} exclude={exc:<3} (currently in file: {have})")

    cur_base = data.get("item_ltx_overrides") or {}
    missing_base = set(baseline) - set(cur_base)
    extra_base = set(cur_base) - set(baseline)
    print(f"baseline in file: {len(cur_base)}  missing={sorted(missing_base)} extra={sorted(extra_base)}")

    if not apply:
        print("\ndry run — pass --apply to write balance.yml")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = BALANCE.with_suffix(f".yml.bak-{stamp}")
    shutil.copy2(BALANCE, backup)
    print(f"\nbackup -> {backup}")

    for fac, fmap in factions.items():
        block = cur_fac.setdefault(fac, {})
        block["item_ltx_overrides"] = fmap
    data["factions"] = cur_fac
    data["item_ltx_overrides"] = baseline
    BALANCE.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    print(f"wrote {BALANCE} ({len(cur_fac)} factions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--apply" in sys.argv))

"""Run dogma_item_stats.script via lupa — SALE's primary stat calculator.

Input gathering (LTX merge, OMF reload_s) stays in Python; scoring math is Lua.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .diaglog import get_logger

log = get_logger("lua_calc")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LUA_PATH = _REPO_ROOT / "src" / "_common" / "scripts" / "dogma_item_stats.script"

_runtime = None
_mod = None
_failed = False

_WEAPON_NUM_KEYS = (
    "hit_power",
    "min_dmg",
    "lgt_dmg",
    "lgtp_dmg",
    "mid_dmg",
    "midp_dmg",
    "min_dps",
    "lgt_dps",
    "lgtp_dps",
    "mid_dps",
    "midp_dps",
    "burst",
    "spread_ads",
    "spread_hip",
    "scope",
    "silencer",
    "reload_s",
    "rpm",
    "mag",
    "cost",
)


def available() -> bool:
    return _module() is not None


def _module():
    global _runtime, _mod, _failed
    if _mod is not None:
        return _mod
    if _failed:
        return None
    try:
        from lupa import LuaRuntime
    except ImportError as exc:
        log.warning("lupa unavailable; SALE will use Python stats_calc mirror: %s", exc)
        _failed = True
        return None
    if not _LUA_PATH.is_file():
        log.error("missing shared Lua calculator: %s", _LUA_PATH)
        _failed = True
        return None
    try:
        _runtime = LuaRuntime(unpack_returned_tuples=True)
        _runtime.execute(_LUA_PATH.read_text(encoding="utf-8"))
        _mod = _runtime.globals().dogma_item_stats
        if _mod is None:
            raise RuntimeError("dogma_item_stats not registered on _G")
        log.info("loaded %s via lupa", _LUA_PATH.name)
        return _mod
    except Exception as exc:  # noqa: BLE001
        log.exception("failed to load dogma_item_stats via lupa: %s", exc)
        _failed = True
        _runtime = None
        _mod = None
        return None


def _to_lua(value: Any):
    assert _runtime is not None
    if isinstance(value, dict):
        t = _runtime.table()
        for k, v in value.items():
            t[k] = _to_lua(v)
        return t
    if isinstance(value, (list, tuple)):
        t = _runtime.table()
        for i, v in enumerate(value, start=1):
            t[i] = _to_lua(v)
        return t
    return value


def _lua_get(tbl, key: str, default: Any = None) -> Any:
    try:
        v = tbl[key]
    except Exception:  # noqa: BLE001
        return default
    if v is None:
        return default
    return v


def _weapon_result(out) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    for k in _WEAPON_NUM_KEYS:
        v = _lua_get(out, k, 0)
        try:
            stats[k] = float(v)
        except (TypeError, ValueError):
            stats[k] = 0.0
    return stats


def _armor_result(out) -> dict[str, Any]:
    from .stats_calc import PROT_KEYS

    stats: dict[str, Any] = {"cost": float(_lua_get(out, "cost", 0) or 0)}
    for k in PROT_KEYS:
        try:
            stats[k] = float(_lua_get(out, k, 0) or 0)
        except (TypeError, ValueError):
            stats[k] = 0.0
    return stats


def weapon_calculate(inp: dict[str, Any]) -> dict[str, Any] | None:
    mod = _module()
    if mod is None:
        return None
    out = mod.weapon_calculate(_to_lua(inp))
    if out is None:
        return None
    return _weapon_result(out)


def armor_calculate(inp: dict[str, Any]) -> dict[str, Any] | None:
    mod = _module()
    if mod is None:
        return None
    out = mod.armor_calculate(_to_lua(inp))
    if out is None:
        return None
    return _armor_result(out)


def sniper_ap_bonus(sec: str, parent_section: str | None = None) -> float:
    mod = _module()
    if mod is None:
        return 0.0
    parent = parent_section or sec
    try:
        return float(mod.sniper_ap_bonus(sec, parent) or 0)
    except Exception:  # noqa: BLE001
        return 0.0


def has_integrated_silencer(sec: str, parent_section: str | None = None) -> bool:
    mod = _module()
    if mod is None:
        return False
    parent = parent_section or sec
    try:
        return bool(mod.has_integrated_silencer(sec, parent))
    except Exception:  # noqa: BLE001
        return False


def calc_backend() -> str:
    return "dogma_item_stats.lupa" if available() else "stats_calc.py"

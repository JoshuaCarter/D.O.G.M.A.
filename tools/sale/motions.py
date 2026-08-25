"""Offline weapon reload timing from HUD anm lines + OGF/OMF motions.

Gathers ``reload_s`` / ``use_mag`` inputs for dogma_item_stats (not scoring math).
Mirrors tip ``weapon_reload_seconds`` / ``weapon_use_mag`` using loose meshes
under Anomaly/GAMMA (same idea as ``game.get_motion_length``).
"""

from __future__ import annotations

import struct
from pathlib import Path

from .diaglog import get_logger
from .ltx_merge import find_mo2_root, parse_modlist

log = get_logger("motions")

SAMPLE_FPS = 30.0
# OGF chunk ids
_OGF_S_MOTION_REFS = 0x13
_OGF_S_MOTION_REFS2 = 0x18
# OMF chunk ids
_OMF_MOTIONS = 0xE
_OMF_PARAMS = 0xF


def iter_mesh_roots(anomaly: Path | None, gamma: Path | None) -> list[Path]:
    """Ordered mesh roots: Anomaly first, then enabled mods (low→high), overwrite."""
    roots: list[Path] = []
    if anomaly:
        for cand in (
            anomaly / "tools" / "_unpacked" / "meshes",
            anomaly / "gamedata" / "meshes",
        ):
            if cand.is_dir():
                roots.append(cand)
                break
    mo2 = find_mo2_root(gamma)
    if mo2 and (mo2 / "mods").is_dir():
        profile_lists = (
            sorted((mo2 / "profiles").glob("*/modlist.txt"))
            if (mo2 / "profiles").is_dir()
            else []
        )
        enabled: list[str] = []
        if profile_lists:
            profile_lists.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            enabled = parse_modlist(profile_lists[0])
        mods_dir = mo2 / "mods"
        for name in reversed(enabled):
            mesh = mods_dir / name / "gamedata" / "meshes"
            if mesh.is_dir():
                roots.append(mesh)
        ow = mo2 / "overwrite" / "gamedata" / "meshes"
        if ow.is_dir():
            roots.append(ow)
    elif gamma and (gamma / "mods").is_dir():
        for mesh in sorted((gamma / "mods").glob("*/gamedata/meshes")):
            if mesh.is_dir():
                roots.append(mesh)
    log.info("mesh roots (%d)", len(roots))
    return roots


def _iter_chunks(data: bytes, start: int = 0, end: int | None = None):
    if end is None:
        end = len(data)
    pos = start
    while pos + 8 <= end:
        cid, size = struct.unpack_from("<II", data, pos)
        if size < 0 or pos + 8 + size > end:
            break
        pos += 8
        yield cid, data[pos : pos + size]
        pos += size


def _read_sz(data: bytes, off: int) -> tuple[str, int]:
    end = data.index(0, off)
    return data[off:end].decode("ascii", "replace"), end + 1


def ogf_motion_refs(data: bytes) -> list[str]:
    refs: list[str] = []
    for cid, payload in _iter_chunks(data):
        if cid == _OGF_S_MOTION_REFS:
            if not payload:
                continue
            try:
                s, _ = _read_sz(payload, 0)
            except ValueError:
                continue
            if s:
                refs.append(s)
        elif cid == _OGF_S_MOTION_REFS2:
            if len(payload) < 4:
                continue
            n = struct.unpack_from("<I", payload, 0)[0]
            off = 4
            limit = n if 0 < n < 256 else 64
            for _ in range(limit):
                if off >= len(payload):
                    break
                try:
                    s, off = _read_sz(payload, off)
                except ValueError:
                    break
                if s:
                    refs.append(s)
    return refs


def omf_motion_seconds(data: bytes) -> dict[str, float]:
    """motion_name → duration seconds at play_speed 1 (includes motion-def speed)."""
    chunks = dict(_iter_chunks(data))
    e = chunks.get(_OMF_MOTIONS)
    if not e:
        return {}
    params = chunks.get(_OMF_PARAMS) or b""
    out: dict[str, float] = {}
    for cid, payload in _iter_chunks(e):
        if cid == 0:
            continue
        try:
            name, off = _read_sz(payload, 0)
            frames = struct.unpack_from("<I", payload, off)[0]
        except (ValueError, struct.error):
            continue
        if not name or frames <= 0:
            continue
        speed = 1.0
        if params:
            idx = params.find(name.encode("ascii") + b"\x00")
            if idx >= 0:
                p = idx + len(name) + 1
                # flags u32, bone_or_part u16, motion u16, speed f32, ...
                if p + 12 <= len(params):
                    try:
                        spd = struct.unpack_from("<f", params, p + 8)[0]
                        if spd and spd > 0:
                            speed = float(spd)
                    except struct.error:
                        pass
        out[name] = (frames / SAMPLE_FPS) / speed
    return out


class MotionLibrary:
    """Resolve HUD anm keys → seconds via item_visual OGF → OMF motions."""

    def __init__(self, anomaly: Path | None, gamma: Path | None) -> None:
        self.roots = iter_mesh_roots(anomaly, gamma)
        self._omf: dict[str, dict[str, float]] = {}
        self._visual_motions: dict[str, dict[str, float]] = {}

    def find_mesh(self, rel: str) -> Path | None:
        rel_n = rel.replace("\\", "/").lstrip("/")
        found: Path | None = None
        for root in self.roots:
            p = root / rel_n
            if p.is_file():
                found = p
        return found

    def motions_for_visual(self, visual: str) -> dict[str, float]:
        vis = (visual or "").strip()
        if not vis:
            return {}
        key = vis.replace("\\", "/").lower()
        if not key.endswith(".ogf"):
            key += ".ogf"
            vis = vis + ".ogf"
        cached = self._visual_motions.get(key)
        if cached is not None:
            return cached
        ogf = self.find_mesh(vis)
        merged: dict[str, float] = {}
        if not ogf:
            self._visual_motions[key] = merged
            return merged
        try:
            refs = ogf_motion_refs(ogf.read_bytes())
        except OSError:
            self._visual_motions[key] = merged
            return merged
        for ref in refs:
            omf_rel = ref.replace("\\", "/")
            if not omf_rel.lower().endswith(".omf"):
                omf_rel += ".omf"
            table = self._omf.get(omf_rel)
            if table is None:
                omf = self.find_mesh(omf_rel)
                if not omf:
                    table = {}
                else:
                    try:
                        table = omf_motion_seconds(omf.read_bytes())
                    except OSError:
                        table = {}
                self._omf[omf_rel] = table
            merged.update(table)
        self._visual_motions[key] = merged
        return merged

    def anm_seconds(self, hud: dict[str, str], anm_key: str) -> float | None:
        raw = (hud.get(anm_key) or "").strip()
        if not raw:
            return None
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if not parts:
            return None
        play_speed = 1.0
        if len(parts) >= 3:
            try:
                ps = float(parts[2])
                if ps > 0:
                    play_speed = ps
            except ValueError:
                pass
        visual = (hud.get("item_visual") or "").strip()
        motions = self.motions_for_visual(visual)
        for name in parts[:2]:
            if name in motions:
                return motions[name] / play_speed
        # LTX anm names often lag reanimation OMFs (skins keep old blnd_* ids).
        fuzzy = _fuzzy_motion_name(anm_key, motions)
        if fuzzy:
            return motions[fuzzy] / play_speed
        return None


def _fuzzy_motion_name(anm_key: str, motions: dict[str, float]) -> str | None:
    """Pick a plausible motion when LTX names don't match OMF entries."""
    if not motions:
        return None
    key = (anm_key or "").lower()
    names = list(motions)

    def pick(cands: list[str]) -> str | None:
        if not cands:
            return None
        # Prefer shorter / more generic names last; heavier empty reloads often
        # include "empty" and are the right empty→full cycle.
        cands = sorted(cands, key=lambda n: (len(n), n.lower()))
        return cands[0]

    if key in ("anm_reload_empty", "anm_reload_1", "anm_reload_2"):
        cands = [
            n
            for n in names
            if "reload" in n.lower()
            and "misfire" not in n.lower()
            and "unjam" not in n.lower()
            and not n.lower().startswith("gl_")
        ]
        empty = [n for n in cands if "empty" in n.lower() or "full" in n.lower()]
        if key == "anm_reload_empty" and empty:
            # Prefer *empty* / *full* (empty→full cycle), avoid "heavy" last.
            empty.sort(
                key=lambda n: (
                    0 if "empty" in n.lower() else 1,
                    1 if "heavy" in n.lower() else 0,
                    len(n),
                    n.lower(),
                )
            )
            return empty[0]
        if key == "anm_reload_2" and empty:
            return pick(empty)
        return pick(cands)
    if key == "anm_reload":
        cands = [
            n
            for n in names
            if "reload" in n.lower()
            and "empty" not in n.lower()
            and "misfire" not in n.lower()
            and "unjam" not in n.lower()
            and not n.lower().startswith("gl_")
        ]
        return pick(cands) or _fuzzy_motion_name("anm_reload_empty", motions)
    if key in ("anm_open_empty", "anm_open"):
        cands = [n for n in names if "open" in n.lower()]
        if key == "anm_open_empty":
            empty = [n for n in cands if "empty" in n.lower()]
            return pick(empty) or pick(cands)
        return pick(cands)
    if key in ("anm_close", "anm_close_empty"):
        cands = [n for n in names if "close" in n.lower()]
        return pick(cands)
    if key in ("anm_add_cartridge", "anm_add_cartridge_empty"):
        cands = [
            n
            for n in names
            if "add" in n.lower() or "load" in n.lower() or "insert" in n.lower()
        ]
        # Avoid open/close/reload-start
        cands = [
            n
            for n in cands
            if "open" not in n.lower() and "close" not in n.lower()
        ]
        if key.endswith("_empty"):
            empty = [n for n in cands if "empty" in n.lower()]
            return pick(empty) or pick(cands)
        return pick(cands)
    return None


def weapon_use_mag(sec: str, sections: dict[str, dict[str, str]]) -> int:
    """1 = magazine cycle, 0 = rounds-reload (tube / break-action / revolver)."""
    d = sections.get(sec) or {}
    cls = (d.get("class") or "").upper()
    tri = (d.get("tri_state_reload") or "").strip().lower()
    hud_name = (d.get("hud") or "").strip()
    hud = sections.get(hud_name) or {}

    if cls == "WP_BM16":
        return 0
    has_r2 = "anm_reload_2" in hud
    has_r = "anm_reload" in hud
    if has_r2 and not has_r:
        return 0
    if tri == "on":
        return 0
    if cls == "WP_REVOLVER":
        return 0
    return 1


def weapon_reload_seconds(
    sec: str,
    sections: dict[str, dict[str, str]],
    motions: MotionLibrary,
) -> float | None:
    """Empty→full hip reload seconds, or None if motions unavailable."""
    d = sections.get(sec) or {}
    hud_name = (d.get("hud") or "").strip()
    if not hud_name:
        return None
    hud = sections.get(hud_name) or {}
    if not hud:
        return None

    cls = (d.get("class") or "").upper()
    tri = (d.get("tri_state_reload") or "").strip().lower()
    try:
        mag = float(d.get("ammo_mag_size") or 0)
    except (TypeError, ValueError):
        mag = 0.0
    n = max(1, int(round(mag)))

    has_r2 = "anm_reload_2" in hud
    has_r = "anm_reload" in hud
    if cls == "WP_BM16" or (has_r2 and not has_r):
        t = motions.anm_seconds(hud, "anm_reload_2")
        if t is None:
            t = motions.anm_seconds(hud, "anm_reload_1")
        return t

    if tri == "on":
        open_t = motions.anm_seconds(hud, "anm_open_empty") or motions.anm_seconds(
            hud, "anm_open"
        )
        add_empty_t = motions.anm_seconds(
            hud, "anm_add_cartridge_empty"
        ) or motions.anm_seconds(hud, "anm_add_cartridge")
        add_t = motions.anm_seconds(hud, "anm_add_cartridge") or add_empty_t
        close_t = motions.anm_seconds(hud, "anm_close") or motions.anm_seconds(
            hud, "anm_close_empty"
        )
        if not (open_t and add_empty_t and add_t and close_t):
            return None
        if n <= 1:
            return open_t + add_empty_t + close_t
        return open_t + add_empty_t + (n - 1) * add_t + close_t

    return motions.anm_seconds(hud, "anm_reload_empty") or motions.anm_seconds(
        hud, "anm_reload"
    )

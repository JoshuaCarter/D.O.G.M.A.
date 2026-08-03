"""Inventory icon thumbs — crop inv_grid cells from ui_icon_equipment DDS."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .diaglog import get_logger

log = get_logger("thumbs")

# Anomaly inventory atlas cell size (pixels per inv_grid unit).
CELL = 50
# Upscale factor when writing PNG thumbs (native crop is CELL×CELL units).
THUMB_SCALE = 2  # outfits / helmets
THUMB_SCALE_WEAPON = 4  # weapons + ammo (2× the armor thumb scale)
DEFAULT_SHEET = "ui/ui_icon_equipment.dds"

_texture_roots: list[Path] = []
_sheet_cache: dict[str, Image.Image] = {}


def set_texture_roots(roots: list[Path]) -> None:
    """Search order: first = lowest priority, last = highest (overwrite)."""
    global _texture_roots, _sheet_cache
    _texture_roots = [Path(r) for r in roots if r and Path(r).is_dir()]
    _sheet_cache.clear()
    log.info("texture roots (%d): %s", len(_texture_roots), [str(r) for r in _texture_roots[:6]])


def iter_texture_roots(anomaly: Path | None, gamma: Path | None) -> list[Path]:
    """Same mod order as configs, but …/gamedata/textures."""
    from .ltx_merge import _parse_modlist, find_mo2_root

    roots: list[Path] = []
    if anomaly:
        for cand in (
            anomaly / "tools" / "_unpacked" / "textures",
            anomaly / "gamedata" / "textures",
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
            enabled = _parse_modlist(profile_lists[0])
        mods_dir = mo2 / "mods"
        # Same priority as configs: modlist is highest-first → apply reversed.
        for name in reversed(enabled):
            tex = mods_dir / name / "gamedata" / "textures"
            if tex.is_dir():
                roots.append(tex)
        ow = mo2 / "overwrite" / "gamedata" / "textures"
        if ow.is_dir():
            roots.append(ow)
    elif gamma and (gamma / "mods").is_dir():
        for tex in sorted((gamma / "mods").glob("*/gamedata/textures")):
            if tex.is_dir():
                roots.append(tex)
    return roots


def _normalize_sheet(icons_texture: str | None) -> str:
    raw = (icons_texture or "").strip().replace("\\", "/")
    if not raw:
        return DEFAULT_SHEET
    if raw.lower().endswith((".dds", ".png", ".tga")):
        return raw
    return raw + ".dds"


def find_dds(icons_texture: str | None = None) -> Path | None:
    found = find_all_dds(icons_texture)
    return found[0] if found else None


def find_all_dds(icons_texture: str | None = None) -> list[Path]:
    """All matching sheets, highest priority first (same logical path can differ per mod)."""
    rel = _normalize_sheet(icons_texture)
    out: list[Path] = []
    seen: set[str] = set()
    for root in reversed(_texture_roots):
        for cand in (root / rel, root / "ui" / Path(rel).name):
            if not cand.is_file():
                continue
            key = str(cand.resolve())
            if key in seen:
                continue
            seen.add(key)
            out.append(cand)
            break
    return out


def _crop_is_empty(img: Image.Image) -> bool:
    """True if crop is fully transparent / near-black (wrong atlas cell)."""
    try:
        rgba = img if img.mode == "RGBA" else img.convert("RGBA")
        ex = rgba.getextrema()
    except Exception:  # noqa: BLE001
        return True
    if not ex or len(ex) < 4:
        return True
    r_max, g_max, b_max, a_max = ex[0][1], ex[1][1], ex[2][1], ex[3][1]
    if a_max <= 8:
        return True
    if max(r_max, g_max, b_max) <= 8 and a_max <= 32:
        return True
    return False


def _load_sheet(path: Path) -> Image.Image | None:
    key = str(path.resolve())
    hit = _sheet_cache.get(key)
    if hit is not None:
        return hit
    try:
        img = Image.open(path)
        img.load()
        img = img.convert("RGBA")
        _sheet_cache[key] = img
        log.debug("loaded sheet %s %sx%s", path.name, img.width, img.height)
        return img
    except Exception as exc:  # noqa: BLE001
        log.warning("DDS open failed %s: %s", path, exc)
        return None


def _grid_ints(fields: dict[str, Any]) -> tuple[int, int, int, int] | None:
    try:
        x = int(float(fields.get("inv_grid_x")))
        y = int(float(fields.get("inv_grid_y")))
        w = int(float(fields.get("inv_grid_width") or 1))
        h = int(float(fields.get("inv_grid_height") or 1))
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return x, y, w, h


def _color_fallback(
    sec: str, out_dir: Path, *, scale: int = THUMB_SCALE
) -> Path | None:
    dest = out_dir / f"{sec}.fallback.png"
    if dest.is_file():
        return dest
    size = 100 * max(1, int(scale))
    h = hashlib.md5(sec.encode("utf-8")).hexdigest()
    color = tuple(max(28, int(h[i : i + 2], 16) // 2) for i in (0, 2, 4))
    img = Image.new("RGBA", (size, size), color + (255,))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, size - 1, size - 1), outline=(70, 70, 70, 255))
    try:
        img.save(dest)
        return dest
    except OSError as exc:
        log.warning("fallback thumb %s: %s", sec, exc)
        return None


def _crop_from_sheet(
    sheet: Image.Image, x: int, y: int, w: int, h: int, sheet_name: str
) -> Image.Image | None:
    left = x * CELL
    top = y * CELL
    right = left + w * CELL
    bottom = top + h * CELL
    if right > sheet.width or bottom > sheet.height or left < 0 or top < 0:
        log.debug(
            "grid OOB %s sheet=%sx%s crop=(%s,%s)-(%s,%s)",
            sheet_name,
            sheet.width,
            sheet.height,
            left,
            top,
            right,
            bottom,
        )
        return None
    return sheet.crop((left, top, right, bottom))


def crop_inv_icon(fields: dict[str, Any]) -> Image.Image | None:
    """Return RGBA crop for inv_grid, or None.

    Tries ``icons_texture`` across every mod copy of that sheet (high→low): a
    higher-priority empty ``ui_icon_ppp`` must not hide a lower pack's real
    cells. Empty/OOB crops fall through to ``ui_icon_equipment``.
    """
    grid = _grid_ints(fields)
    if not grid:
        return None
    x, y, w, h = grid
    custom = (str(fields.get("icons_texture") or "")).strip()
    tried: list[str] = []

    def _try(logical: str | None) -> Image.Image | None:
        for dds in find_all_dds(logical):
            key = str(dds.resolve())
            if key in tried:
                continue
            tried.append(key)
            sheet = _load_sheet(dds)
            if sheet is None:
                continue
            crop = _crop_from_sheet(sheet, x, y, w, h, dds.name)
            if crop is None or _crop_is_empty(crop):
                continue
            return crop
        return None

    if custom:
        hit = _try(custom)
        if hit is not None:
            return hit
        log.debug(
            "custom icons_texture crop failed (%s) — trying ui_icon_equipment",
            custom,
        )

    return _try(None)


def make_thumb(
    sec: str,
    fields: dict[str, Any],
    out_dir: Path,
    *,
    icon_bundle: dict[str, Any] | None = None,
    parent_icon_bundle: dict[str, Any] | None = None,
    scale: int | None = None,
) -> Path | None:
    """Write PNG thumb (real inv_grid crop when possible).

    ``scale`` upscales the native CELL crop (default ``THUMB_SCALE``).
    Weapons/ammo use ``THUMB_SCALE_WEAPON`` (2× armor thumbs).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{sec}.inv.png"
    if dest.is_file():
        return dest
    thumb_scale = THUMB_SCALE if scale is None else max(1, int(scale))

    def _bundle_attempts(bundle: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not bundle:
            return []
        coherent = dict(fields)
        coherent.update(bundle)
        hybrid = dict(fields)
        if bundle.get("icons_texture"):
            hybrid["icons_texture"] = bundle["icons_texture"]
        return [coherent, hybrid]

    attempts: list[dict[str, Any]] = []
    # Own icons_texture snapshot first (fixes Frankenstein inv_grid_x patches).
    attempts.extend(_bundle_attempts(icon_bundle))
    attempts.append(fields)
    # Boomsticks grids often target ui_icon_bas while icons_texture drifts to PPP/etc.
    bas = dict(fields)
    bas["icons_texture"] = "ui\\ui_icon_bas"
    attempts.append(bas)
    # Parent bundle last — can be a wrong relative (e.g. recoil patch :wpn_pb).
    attempts.extend(_bundle_attempts(parent_icon_bundle))

    for attempt in attempts:
        crop = crop_inv_icon(attempt)
        if crop is None or _crop_is_empty(crop):
            continue
        try:
            if thumb_scale != 1:
                crop = crop.resize(
                    (crop.width * thumb_scale, crop.height * thumb_scale),
                    Image.Resampling.LANCZOS,
                )
            crop.save(dest)
            fb = out_dir / f"{sec}.fallback.png"
            if fb.is_file():
                try:
                    fb.unlink()
                except OSError:
                    pass
            return dest
        except OSError as exc:
            log.warning("save crop %s: %s", sec, exc)

    return _color_fallback(sec, out_dir, scale=thumb_scale)

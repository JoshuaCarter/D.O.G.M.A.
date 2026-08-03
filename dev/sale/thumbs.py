"""Inventory icon thumbs — placeholder colored tiles (full inv_grid crop later)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .diaglog import get_logger

log = get_logger("thumbs")


def make_thumb(sec: str, fields: dict[str, Any], out_dir: Path) -> Path | None:
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{sec}.png"
    if dest.is_file():
        return dest
    h = hashlib.md5(sec.encode("utf-8")).hexdigest()
    color = tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))
    img = Image.new("RGBA", (64, 32), color + (255,))
    draw = ImageDraw.Draw(img)
    label = sec.replace("wpn_", "").replace("outfit_", "")[:10]
    try:
        font = ImageFont.load_default()
        draw.text((2, 10), label, fill=(255, 255, 255, 255), font=font)
    except Exception:  # noqa: BLE001
        pass
    try:
        img.save(dest)
        return dest
    except OSError as exc:
        log.warning("thumb %s: %s", sec, exc)
        return None

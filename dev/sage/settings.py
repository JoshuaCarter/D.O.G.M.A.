"""Persistent settings: texture / textures_descr search roots."""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = Path(__file__).resolve().parent / "settings.json"

UI_WIDTH = 1024
UI_HEIGHT = 768

LABEL_FONT_MIN = 2
LABEL_FONT_MAX = 40
LABEL_FONT_DEFAULT = 8
RECENT_FILES_MAX = 10


def clamp_label_font_size(value: object) -> int:
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return LABEL_FONT_DEFAULT
    return max(LABEL_FONT_MIN, min(LABEL_FONT_MAX, n))


def normalize_recent_files(value: object) -> list[str]:
    """Unique existing-or-remembered paths, newest first, capped."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            continue
        try:
            key = str(Path(item).expanduser().resolve())
        except OSError:
            key = str(Path(item).expanduser())
        low = key.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(key)
        if len(out) >= RECENT_FILES_MAX:
            break
    return out


def push_recent_file(settings: dict, path: Path) -> list[str]:
    """Prepend path to settings['recent_files']; returns the new list."""
    try:
        key = str(path.expanduser().resolve())
    except OSError:
        key = str(path.expanduser())
    prev = normalize_recent_files(settings.get("recent_files"))
    merged = [key] + [p for p in prev if p.lower() != key.lower()]
    recent = merged[:RECENT_FILES_MAX]
    settings["recent_files"] = recent
    return recent

# Common local installs (first existing wins per slot; all existing are used)
_CANDIDATE_TEXTURE_DIRS = (
    Path(r"C:/GAMMA/mods/G.A.M.M.A. UI/gamedata/textures"),
    Path(r"C:/GAMMA/.Grok's Modpack Installer/G.A.M.M.A/modpack_addons/G.A.M.M.A. UI/gamedata/textures"),
    Path(r"C:/Anomaly/gamedata/textures"),
    Path(r"C:/gamma_dev/_db_unpacked/textures"),
)
_CANDIDATE_DESCR_DIRS = (
    Path(r"C:/gamma_dev/_db_unpacked/configs/ui/textures_descr"),
    Path(r"C:/Anomaly/tools/_unpacked/configs/ui/textures_descr"),
    Path(r"C:/GAMMA/mods/G.A.M.M.A. UI/gamedata/configs/ui/textures_descr"),
    Path(r"C:/Anomaly/gamedata/configs/ui/textures_descr"),
)
_CANDIDATE_TEXT_DIRS = (
    Path(r"C:/Anomaly/tools/_unpacked/configs/text/eng"),
    Path(r"C:/gamma_dev/_db_unpacked/configs/text/eng"),
    Path(r"C:/Anomaly/gamedata/configs/text/eng"),
    Path(
        r"C:/GAMMA/mods/287- G.A.M.M.A. Massive Text Overhaul Project - "
        r"SageDaHerb and Dr.Pr1nkos/gamedata/configs/text/eng"
    ),
)


def _existing(paths: tuple[Path, ...] | list[Path]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for p in paths:
        try:
            if p.is_dir():
                key = str(p.resolve())
            else:
                continue
        except OSError:
            continue
        if key not in seen:
            seen.add(key)
            out.append(str(p))
    return out


def default_settings() -> dict:
    return {
        # Scanned for **/textures and **/textures_descr (DOGMA overrides last)
        "texture_roots": [str(REPO_ROOT / "src")],
        "textures_descr_roots": [str(REPO_ROOT / "src")],
        "text_roots": [str(REPO_ROOT / "src")],
        # Direct folders - auto-detected Anomaly/GAMMA installs
        "gamedata_texture_roots": _existing(_CANDIDATE_TEXTURE_DIRS),
        "gamedata_descr_roots": _existing(_CANDIDATE_DESCR_DIRS),
        "gamedata_text_roots": _existing(_CANDIDATE_TEXT_DIRS),
        "show_grid": True,
        "grid_step": 16,
        "label_font_size": LABEL_FONT_DEFAULT,
        "show_element_labels": True,
        "show_box_border": False,
        "show_box_fill": False,
        "scroll_select": False,
        "last_file_dir": str(REPO_ROOT / "src"),
        "recent_files": [],
        "window": {
            "x": None,
            "y": None,
            "width": 1400,
            "height": 900,
            "maximized": False,
        },
    }


def load_settings() -> dict:
    data = default_settings()
    if SETTINGS_PATH.is_file():
        try:
            loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                # Merge list keys: keep user overrides if non-empty, else keep defaults
                for key, value in loaded.items():
                    if key in (
                        "gamedata_texture_roots",
                        "gamedata_descr_roots",
                        "gamedata_text_roots",
                        "texture_roots",
                        "textures_descr_roots",
                        "text_roots",
                    ):
                        if isinstance(value, list) and value:
                            data[key] = value
                        # empty list in file → keep auto-detected defaults
                    else:
                        data[key] = value
        except (OSError, json.JSONDecodeError):
            pass
    data["label_font_size"] = clamp_label_font_size(data.get("label_font_size"))
    data["show_element_labels"] = bool(data.get("show_element_labels", True))
    data["recent_files"] = normalize_recent_files(data.get("recent_files"))
    # Re-merge auto-detect if still empty (fresh install / cleared settings)
    if not data.get("gamedata_texture_roots"):
        data["gamedata_texture_roots"] = _existing(_CANDIDATE_TEXTURE_DIRS)
    if not data.get("gamedata_descr_roots"):
        data["gamedata_descr_roots"] = _existing(_CANDIDATE_DESCR_DIRS)
    if not data.get("gamedata_text_roots"):
        data["gamedata_text_roots"] = _existing(_CANDIDATE_TEXT_DIRS)
    if not data.get("text_roots"):
        data["text_roots"] = [str(REPO_ROOT / "src")]
    return data


def save_settings(data: dict) -> None:
    SETTINGS_PATH.write_text(
        json.dumps(data, indent=2) + "\n",
        encoding="utf-8",
    )

"""Persistent settings: GAMMA / Anomaly roots → derived asset paths."""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = Path(__file__).resolve().parent / "settings.json"

UI_WIDTH = 1024
UI_HEIGHT = 768

LABEL_FONT_MIN = 2
LABEL_FONT_MAX = 40
LABEL_FONT_DEFAULT = 5
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


def _norm_root(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        p = Path(value).expanduser()
        if p.is_dir():
            return str(p.resolve())
        return str(p)
    except OSError:
        return str(Path(value).expanduser())


def validate_anomaly_root(path: object) -> tuple[bool, str]:
    """Anomaly root must contain tools/db_unpacker.bat."""
    raw = str(path or "").strip()
    if not raw:
        return False, "Choose the Anomaly install folder."
    root = Path(raw).expanduser()
    if not root.is_dir():
        return False, f"Folder not found: {root}"
    marker = root / "tools" / "db_unpacker.bat"
    if not marker.is_file():
        return False, f"Missing {marker.name} under tools\\ (not an Anomaly root?)"
    return True, str(marker)


def validate_gamma_root(path: object) -> tuple[bool, str]:
    """GAMMA root must contain mods/G.A.M.M.A. UI/gamedata/textures."""
    raw = str(path or "").strip()
    if not raw:
        return False, "Choose the G.A.M.M.A. install folder."
    root = Path(raw).expanduser()
    if not root.is_dir():
        return False, f"Folder not found: {root}"
    marker = root / "mods" / "G.A.M.M.A. UI" / "gamedata" / "textures"
    if not marker.is_dir():
        return False, f"Missing mods\\G.A.M.M.A. UI\\gamedata\\textures"
    return True, str(marker)


def installs_configured(settings: dict) -> bool:
    a_ok, _ = validate_anomaly_root(settings.get("anomaly_root"))
    g_ok, _ = validate_gamma_root(settings.get("gamma_root"))
    return a_ok and g_ok


def normalize_custom_roots(value: object) -> list[str]:
    """Unique existing directories to scan for textures / text / future assets."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            continue
        try:
            p = Path(item).expanduser()
            if not p.is_dir():
                continue
            key = str(p.resolve())
        except OSError:
            continue
        low = key.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(key)
    return out


def summarize_custom_root(path: object) -> str:
    """Short note of what well-known asset folders exist under a custom root."""
    raw = str(path or "").strip()
    if not raw:
        return ""
    root = Path(raw).expanduser()
    if not root.is_dir():
        return "folder not found"
    found: list[str] = []
    tex_paths, descr_paths, text_paths = _known_asset_paths_under(root)
    if any(p.is_dir() for p in tex_paths):
        found.append("textures")
    if any(p.is_dir() for p in descr_paths):
        found.append("textures_descr")
    if any(p.is_dir() for p in text_paths):
        found.append("text")
    if not found:
        return "will be scanned recursively"
    return "found: " + ", ".join(found)


def _existing_dirs(paths: list[Path]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for p in paths:
        try:
            if not p.is_dir():
                continue
            key = str(p.resolve())
        except OSError:
            continue
        low = key.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(key)
    return out


def _gamma_ui_mod_bases(gamma: Path) -> list[Path]:
    """G.A.M.M.A. UI install locations under a GAMMA root."""
    return [
        gamma / "mods" / "G.A.M.M.A. UI",
        gamma
        / ".Grok's Modpack Installer"
        / "G.A.M.M.A"
        / "modpack_addons"
        / "G.A.M.M.A. UI",
    ]


def _gamma_text_overhaul_roots(gamma: Path) -> list[Path]:
    """Mods that ship configs/text/eng (e.g. Massive Text Overhaul)."""
    mods = gamma / "mods"
    found: list[Path] = []
    if not mods.is_dir():
        return found
    try:
        for child in mods.iterdir():
            if not child.is_dir():
                continue
            name = child.name.lower()
            if "text" in name and ("overhaul" in name or "massive" in name):
                found.append(child / "gamedata" / "configs" / "text" / "eng")
    except OSError:
        pass
    return found


def _known_asset_paths_under(root: Path) -> tuple[list[Path], list[Path], list[Path]]:
    """Pick well-known texture / descr / text folders under an arbitrary root."""
    tex = [
        root / "gamedata" / "textures",
        root / "textures",
    ]
    descr = [
        root / "gamedata" / "configs" / "ui" / "textures_descr",
        root / "configs" / "ui" / "textures_descr",
        root / "textures_descr",
    ]
    text = [
        root / "gamedata" / "configs" / "text" / "eng",
        root / "configs" / "text" / "eng",
        root / "text" / "eng",
    ]
    return tex, descr, text


def derived_asset_roots(
    *,
    anomaly_root: str = "",
    gamma_root: str = "",
    custom_roots: list[str] | None = None,
) -> dict[str, list[str]]:
    """Build texture / descr / text root lists from user install roots + DOGMA src.

    Canonical lookups given Anomaly + GAMMA roots (only existing dirs kept):

    textures
      {GAMMA}/mods/G.A.M.M.A. UI/gamedata/textures
      {GAMMA}/.Grok's Modpack Installer/G.A.M.M.A/modpack_addons/G.A.M.M.A. UI/gamedata/textures
      {Anomaly}/gamedata/textures
      {Anomaly}/tools/_unpacked/textures

    textures_descr
      {Anomaly}/tools/_unpacked/configs/ui/textures_descr
      {GAMMA}/mods/G.A.M.M.A. UI/gamedata/configs/ui/textures_descr

    text (eng)
      {Anomaly}/tools/_unpacked/configs/text/eng
      {Anomaly}/gamedata/configs/text/eng
      {GAMMA}/mods/<Massive Text Overhaul…>/gamedata/configs/text/eng
    """
    dogma_src = REPO_ROOT / "src"
    tex: list[Path] = []
    descr: list[Path] = []
    text: list[Path] = []
    scan_tex = [dogma_src]
    scan_descr = [dogma_src]
    scan_text = [dogma_src]

    anomaly = Path(anomaly_root) if anomaly_root.strip() else None
    gamma = Path(gamma_root) if gamma_root.strip() else None

    # --- textures (base → override: Anomaly, then GAMMA UI packs) ---
    if anomaly is not None:
        tex.append(anomaly / "gamedata" / "textures")
        tex.append(anomaly / "tools" / "_unpacked" / "textures")
    if gamma is not None:
        for base in _gamma_ui_mod_bases(gamma):
            tex.append(base / "gamedata" / "textures")

    # --- textures_descr ---
    if anomaly is not None:
        descr.append(anomaly / "tools" / "_unpacked" / "configs" / "ui" / "textures_descr")
        descr.append(anomaly / "gamedata" / "configs" / "ui" / "textures_descr")
    if gamma is not None:
        for base in _gamma_ui_mod_bases(gamma):
            descr.append(base / "gamedata" / "configs" / "ui" / "textures_descr")

    # --- text / eng ---
    if anomaly is not None:
        text.append(anomaly / "tools" / "_unpacked" / "configs" / "text" / "eng")
        text.append(anomaly / "gamedata" / "configs" / "text" / "eng")
    if gamma is not None:
        text.extend(_gamma_text_overhaul_roots(gamma))
        for base in _gamma_ui_mod_bases(gamma):
            text.append(base / "gamedata" / "configs" / "text" / "eng")

    # Recursive scan under DOGMA src + GAMMA mods + Grok installer addons
    if gamma is not None:
        scan_tex.append(gamma / "mods")
        scan_descr.append(gamma / "mods")
        scan_text.append(gamma / "mods")
        grok_addons = (
            gamma
            / ".Grok's Modpack Installer"
            / "G.A.M.M.A"
            / "modpack_addons"
        )
        scan_tex.append(grok_addons)
        scan_descr.append(grok_addons)
        scan_text.append(grok_addons)

    for raw in custom_roots or []:
        if not str(raw).strip():
            continue
        custom = Path(str(raw).strip()).expanduser()
        if not custom.is_dir():
            continue
        scan_tex.append(custom)
        scan_descr.append(custom)
        scan_text.append(custom)
        c_tex, c_descr, c_text = _known_asset_paths_under(custom)
        tex.extend(c_tex)
        descr.extend(c_descr)
        text.extend(c_text)

    return {
        "texture_roots": _existing_dirs(scan_tex) or [str(dogma_src)],
        "textures_descr_roots": _existing_dirs(scan_descr) or [str(dogma_src)],
        "text_roots": _existing_dirs(scan_text) or [str(dogma_src)],
        "gamedata_texture_roots": _existing_dirs(tex),
        "gamedata_descr_roots": _existing_dirs(descr),
        "gamedata_text_roots": _existing_dirs(text),
    }


def apply_install_roots(settings: dict) -> dict:
    """Fill derived asset path lists from install + optional custom roots."""
    settings["anomaly_root"] = _norm_root(settings.get("anomaly_root", ""))
    settings["gamma_root"] = _norm_root(settings.get("gamma_root", ""))
    settings["custom_roots"] = normalize_custom_roots(settings.get("custom_roots"))
    derived = derived_asset_roots(
        anomaly_root=str(settings.get("anomaly_root") or ""),
        gamma_root=str(settings.get("gamma_root") or ""),
        custom_roots=list(settings.get("custom_roots") or []),
    )
    settings.update(derived)
    return settings

def ensure_db_unpacked_and_roots(settings: dict | None = None) -> dict:
    """Unpack Anomaly DBs if needed, then refresh derived roots from install dirs."""
    from .db_unpack import ensure_anomaly_db_unpacked

    data = settings if settings is not None else default_settings()
    anomaly = str(data.get("anomaly_root") or "").strip()
    ensure_anomaly_db_unpacked(anomaly_root=anomaly or None)
    return apply_install_roots(data)


def default_settings() -> dict:
    # Roots stay empty until the user sets them (setup / settings). Detect is
    # only used to prefill the setup dialog, not to skip it.
    data = {
        "anomaly_root": "",
        "gamma_root": "",
        "custom_roots": [],
        "show_grid": False,
        "grid_step": 16,
        "label_font_size": LABEL_FONT_DEFAULT,
        "show_element_labels": False,
        "show_box_border": False,
        "show_box_fill": False,
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
    apply_install_roots(data)
    return data


def _infer_root_from_paths(paths: object, needle: str) -> str:
    if not isinstance(paths, list):
        return ""
    needle_l = needle.lower()
    for raw in paths:
        if not isinstance(raw, str):
            continue
        try:
            p = Path(raw)
            parts = [x.lower() for x in p.parts]
            if needle_l in parts:
                idx = parts.index(needle_l)
                return str(Path(*p.parts[: idx + 1]))
        except (OSError, ValueError):
            continue
    return ""


def load_settings() -> dict:
    data = default_settings()
    if SETTINGS_PATH.is_file():
        try:
            loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                for key, value in loaded.items():
                    # Derived path lists are always recomputed from install roots.
                    if key in (
                        "texture_roots",
                        "textures_descr_roots",
                        "text_roots",
                        "gamedata_texture_roots",
                        "gamedata_descr_roots",
                        "gamedata_text_roots",
                    ):
                        continue
                    data[key] = value
        except (OSError, json.JSONDecodeError):
            pass

    # Migrate older settings that only had path lists (no install roots yet).
    if not str(data.get("anomaly_root") or "").strip():
        inferred = _infer_root_from_paths(
            data.get("gamedata_texture_roots"), "Anomaly"
        ) or _infer_root_from_paths(data.get("gamedata_descr_roots"), "Anomaly")
        if inferred:
            data["anomaly_root"] = inferred
    if not str(data.get("gamma_root") or "").strip():
        inferred = _infer_root_from_paths(
            data.get("gamedata_texture_roots"), "GAMMA"
        ) or _infer_root_from_paths(data.get("gamedata_descr_roots"), "GAMMA")
        if inferred:
            data["gamma_root"] = inferred

    data["label_font_size"] = clamp_label_font_size(data.get("label_font_size"))
    data["show_element_labels"] = bool(data.get("show_element_labels", False))
    data["recent_files"] = normalize_recent_files(data.get("recent_files"))
    data["custom_roots"] = normalize_custom_roots(data.get("custom_roots"))
    # Derive paths only — unpack is prompted from Setup / Settings / Reload.
    apply_install_roots(data)
    return data


def save_settings(data: dict) -> None:
    apply_install_roots(data)
    SETTINGS_PATH.write_text(
        json.dumps(data, indent=2) + "\n",
        encoding="utf-8",
    )

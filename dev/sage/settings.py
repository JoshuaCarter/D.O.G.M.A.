"""Persistent settings: GAMMA / Anomaly roots → derived asset paths."""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = Path(__file__).resolve().parent / "settings.json"

UI_WIDTH = 1024
UI_HEIGHT = 768
# Canvas / atlas view zoom floor/ceiling (slider + wheel). Native = 1.0.
ZOOM_SCALE_MIN = 1.0
ZOOM_SCALE_MAX = 10.0

# Editor-only preview aspect (never written into XML). "" = native stage ratio.
PREVIEW_ASPECT_NATIVE = ""
PREVIEW_ASPECT_PRESETS: dict[str, float] = {
    "4:3": 4.0 / 3.0,
    "16:9": 16.0 / 9.0,
    "16:10": 16.0 / 10.0,
}


def native_ui_aspect() -> float:
    return float(UI_WIDTH) / float(UI_HEIGHT)


def normalize_preview_aspect(value: object) -> str:
    """Return a preset key or '' for native. Unknown / native-equal → ''."""
    key = str(value or "").strip()
    if key not in PREVIEW_ASPECT_PRESETS:
        return PREVIEW_ASPECT_NATIVE
    if abs(PREVIEW_ASPECT_PRESETS[key] - native_ui_aspect()) < 1e-6:
        return PREVIEW_ASPECT_NATIVE
    return key


def preview_aspect_stretch_x(value: object) -> float:
    """Horizontal view stretch vs native stage (text should counter-scale by 1/sx)."""
    key = normalize_preview_aspect(value)
    if not key:
        return 1.0
    return PREVIEW_ASPECT_PRESETS[key] / native_ui_aspect()


def preview_aspect_combo_items() -> list[tuple[str, str]]:
    """``(label, data)`` for the Aspect combo. Data ``''`` = native stage."""
    native = native_ui_aspect()
    default_label = f"{UI_WIDTH}:{UI_HEIGHT}"
    for name, ratio in PREVIEW_ASPECT_PRESETS.items():
        if abs(ratio - native) < 1e-6:
            default_label = name
            break
    items: list[tuple[str, str]] = [(default_label, PREVIEW_ASPECT_NATIVE)]
    for name, ratio in PREVIEW_ASPECT_PRESETS.items():
        if abs(ratio - native) < 1e-6:
            continue
        items.append((name, name))
    return items

# Font preview: engine picks atlas + scales by Device.dwHeight (see fonts.py).
DEFAULT_FONT_DEVICE_HEIGHT = 1080
# 0 / missing → auto from Anomaly appdata/user.ltx vid_mode.

LABEL_FONT_MIN = 2
LABEL_FONT_MAX = 40
LABEL_FONT_DEFAULT = 5
RECENT_FILES_MAX = 10
# Bump when derived root ordering / discovery rules change (forces one rescan).
ASSET_ROOTS_VERSION = 5


def clamp_font_device_height(value: object) -> int:
    """Positive device height for font preview; 0 means auto-detect."""
    try:
        h = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    if h <= 0:
        return 0
    return max(480, min(8700, h))


def read_vid_mode_height(anomaly_root: object) -> int | None:
    """Parse ``vid_mode WxH`` from ``<anomaly>/appdata/user.ltx``."""
    root = str(anomaly_root or "").strip()
    if not root:
        return None
    path = Path(root) / "appdata" / "user.ltx"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(";") or s.startswith("//"):
            continue
        # vid_mode 3840x2160
        if not s.lower().startswith("vid_mode"):
            continue
        rest = s.split(None, 1)
        if len(rest) < 2:
            continue
        mode = rest[1].strip().split()[0]
        if "x" not in mode.lower():
            continue
        _w, _, h_s = mode.lower().partition("x")
        try:
            h = int(float(h_s))
        except ValueError:
            return None
        return clamp_font_device_height(h) or None
    return None


def resolve_font_device_height(settings: dict) -> int:
    """Effective Device.dwHeight for SAGE font preview (override or user.ltx)."""
    override = clamp_font_device_height(settings.get("font_device_height", 0))
    if override > 0:
        return override
    detected = read_vid_mode_height(settings.get("anomaly_root"))
    if detected is not None and detected > 0:
        return detected
    return DEFAULT_FONT_DEVICE_HEIGHT


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


def _resolved_path_key(path: Path | str) -> str:
    try:
        return str(Path(path).expanduser().resolve())
    except OSError:
        return str(Path(path).expanduser())


def normalize_deploy_targets(value: object) -> dict[str, str]:
    """Map source XML path → deploy target path (resolved strings)."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for raw_src, raw_dst in value.items():
        if not isinstance(raw_src, str) or not isinstance(raw_dst, str):
            continue
        if not raw_src.strip() or not raw_dst.strip():
            continue
        out[_resolved_path_key(raw_src)] = _resolved_path_key(raw_dst)
    return out


def set_deploy_target(settings: dict, source: Path, target: Path) -> None:
    """Remember deploy overwrite path for a source file."""
    targets = normalize_deploy_targets(settings.get("deploy_targets"))
    targets[_resolved_path_key(source)] = _resolved_path_key(target)
    settings["deploy_targets"] = targets
    parent = target if target.is_dir() else target.parent
    try:
        if parent.is_dir():
            settings["last_deploy_dir"] = str(parent.resolve())
    except OSError:
        settings["last_deploy_dir"] = str(parent)


def get_deploy_target(settings: dict, source: Path | None) -> Path | None:
    if source is None:
        return None
    targets = normalize_deploy_targets(settings.get("deploy_targets"))
    key = _resolved_path_key(source)
    raw = targets.get(key)
    if not raw:
        # Case-insensitive fallback (Windows).
        low = key.lower()
        for sk, tv in targets.items():
            if sk.lower() == low:
                raw = tv
                break
    if not raw:
        return None
    return Path(raw)


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


def _mo2_modlist_path(gamma: Path) -> Path | None:
    """Active MO2 profile modlist under a GAMMA (or MO2 instance) root."""
    for candidate in (
        gamma / "profiles" / "G.A.M.M.A" / "modlist.txt",
        gamma / "profiles" / "Default" / "modlist.txt",
    ):
        if candidate.is_file():
            return candidate
    profiles = gamma / "profiles"
    if not profiles.is_dir():
        return None
    try:
        children = sorted(profiles.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return None
    for child in children:
        ml = child / "modlist.txt"
        if ml.is_file():
            return ml
    return None


def _parse_mo2_enabled_mods(modlist: Path) -> list[str]:
    """Enabled mod folder names, highest priority first (MO2 file order)."""
    try:
        lines = modlist.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    names: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or not line.startswith("+"):
            continue
        name = line[1:].strip()
        if not name or name.lower().endswith("_separator"):
            continue
        if "separator" in name.lower() and name.endswith("separator"):
            continue
        names.append(name)
    return names


def _pack_sort_key(name: str) -> tuple[int, str]:
    """Numeric GAMMA prefix first (109- …), then casefold name."""
    m = re.match(r"^(\d+)", name)
    if m:
        return (int(m.group(1)), name.casefold())
    return (10**9, name.casefold())


def _iter_gamma_pack_dirs(gamma: Path) -> list[Path]:
    """Installed packs in override order: lowest priority → highest (last wins).

    Prefers MO2 ``profiles/.../modlist.txt`` (top of list = highest priority).
    Falls back to ``mods/`` sorted by numeric prefix — does not append Grok
    installer copies after live mods.
    """
    mods_dir = gamma / "mods"
    out: list[Path] = []
    seen: set[str] = set()

    def _add(pack: Path) -> None:
        if not pack.is_dir():
            return
        try:
            key = str(pack.resolve()).lower()
        except OSError:
            key = str(pack).lower()
        if key in seen:
            return
        seen.add(key)
        out.append(pack)

    ml = _mo2_modlist_path(gamma)
    if ml is not None and mods_dir.is_dir():
        # MO2: first lines = highest priority → process reversed for last-wins.
        for name in reversed(_parse_mo2_enabled_mods(ml)):
            _add(mods_dir / name)
        return out

    if mods_dir.is_dir():
        try:
            children = [c for c in mods_dir.iterdir() if c.is_dir()]
        except OSError:
            children = []
        for pack in sorted(children, key=lambda p: _pack_sort_key(p.name)):
            _add(pack)
    return out


def _pack_asset_paths(pack: Path) -> tuple[list[Path], list[Path], list[Path]]:
    """Well-known asset folders under one mod/addon pack."""
    return (
        [pack / "gamedata" / "textures"],
        [pack / "gamedata" / "configs" / "ui" / "textures_descr"],
        [pack / "gamedata" / "configs" / "text" / "eng"],
    )


def _gamma_text_overhaul_roots(gamma: Path) -> list[Path]:
    """Mods that ship configs/text/eng (e.g. Massive Text Overhaul)."""
    found: list[Path] = []
    for pack in _iter_gamma_pack_dirs(gamma):
        name = pack.name.lower()
        if "text" in name and ("overhaul" in name or "massive" in name):
            found.append(pack / "gamedata" / "configs" / "text" / "eng")
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
    """Build texture / descr / text root lists from installs + user custom dirs.

    Resolution order (last wins): Anomaly → GAMMA (MO2 modlist) → user
    ``custom_roots`` (always last). No project paths are implied — add source
    and/or deployed mod folders via custom roots in Setup/Settings.
    """
    tex: list[Path] = []
    descr: list[Path] = []
    text: list[Path] = []
    scan_tex: list[Path] = []
    scan_descr: list[Path] = []
    scan_text: list[Path] = []

    anomaly = Path(anomaly_root) if anomaly_root.strip() else None
    gamma = Path(gamma_root) if gamma_root.strip() else None

    if anomaly is not None:
        # DB extract first; loose Anomaly gamedata overrides it (last wins).
        tex.append(anomaly / "tools" / "_unpacked" / "textures")
        tex.append(anomaly / "gamedata" / "textures")
        descr.append(anomaly / "tools" / "_unpacked" / "configs" / "ui" / "textures_descr")
        descr.append(anomaly / "gamedata" / "configs" / "ui" / "textures_descr")
        text.append(anomaly / "tools" / "_unpacked" / "configs" / "text" / "eng")
        text.append(anomaly / "gamedata" / "configs" / "text" / "eng")

    if gamma is not None:
        # Packs already ordered low→high priority (MO2 modlist / numeric fallback).
        for pack in _iter_gamma_pack_dirs(gamma):
            p_tex, p_descr, p_text = _pack_asset_paths(pack)
            descr_dir = pack / "gamedata" / "configs" / "ui" / "textures_descr"
            ui_cfg = pack / "gamedata" / "configs" / "ui"
            name_l = pack.name.lower()
            # Textures: any pack that ships them (do not require textures_descr).
            tex.extend(p_tex)
            descr.extend(p_descr)
            # Text: UI / text-overhaul packs, or anything that ships UI configs
            # alongside string tables (covers deployed project mods like DOGMA).
            is_text_pack = "text" in name_l and (
                "overhaul" in name_l or "massive" in name_l
            )
            is_named_ui = bool(
                re.search(r"(^|[^a-z0-9])ui([^a-z0-9]|$)", name_l)
            )
            has_ui_assets = descr_dir.is_dir() or ui_cfg.is_dir()
            if is_text_pack or is_named_ui or has_ui_assets:
                text.extend(p_text)

    # User-supplied dirs only — always last in resolution order.
    for raw in custom_roots or []:
        if not str(raw).strip():
            continue
        custom = Path(str(raw).strip()).expanduser()
        if not custom.is_dir():
            continue
        scan_tex.append(custom)
        scan_descr.append(custom)
        scan_text.append(custom)

    return {
        "texture_roots": _existing_dirs(scan_tex),
        "textures_descr_roots": _existing_dirs(scan_descr),
        "text_roots": _existing_dirs(scan_text),
        "gamedata_texture_roots": _existing_dirs(tex),
        "gamedata_descr_roots": _existing_dirs(descr),
        "gamedata_text_roots": _existing_dirs(text),
    }


_ASSET_ROOT_KEYS = (
    "texture_roots",
    "textures_descr_roots",
    "text_roots",
    "gamedata_texture_roots",
    "gamedata_descr_roots",
    "gamedata_text_roots",
)


def _empty_asset_roots() -> dict[str, list[str]]:
    return {
        "texture_roots": [],
        "textures_descr_roots": [],
        "text_roots": [],
        "gamedata_texture_roots": [],
        "gamedata_descr_roots": [],
        "gamedata_text_roots": [],
    }


def rescan_asset_roots(settings: dict) -> dict:
    """Discover asset folders from Anomaly / GAMMA / custom roots and store path lists.

    Call from Setup / Settings / Rescan menu — not on every settings save or launch.
    """
    settings["anomaly_root"] = _norm_root(settings.get("anomaly_root", ""))
    settings["gamma_root"] = _norm_root(settings.get("gamma_root", ""))
    settings["custom_roots"] = normalize_custom_roots(settings.get("custom_roots"))
    derived = derived_asset_roots(
        anomaly_root=str(settings.get("anomaly_root") or ""),
        gamma_root=str(settings.get("gamma_root") or ""),
        custom_roots=list(settings.get("custom_roots") or []),
    )
    settings.update(derived)
    settings["asset_roots_version"] = ASSET_ROOTS_VERSION
    return settings


def rescan_custom_asset_roots(settings: dict) -> dict:
    """Refresh only custom scan roots; leave Anomaly / GAMMA path lists untouched."""
    settings["custom_roots"] = normalize_custom_roots(settings.get("custom_roots"))
    derived = derived_asset_roots(
        anomaly_root="",
        gamma_root="",
        custom_roots=list(settings.get("custom_roots") or []),
    )
    settings["texture_roots"] = derived["texture_roots"]
    settings["textures_descr_roots"] = derived["textures_descr_roots"]
    settings["text_roots"] = derived["text_roots"]
    return settings


# Back-compat alias
apply_install_roots = rescan_asset_roots


def ensure_db_unpacked_and_roots(settings: dict | None = None) -> dict:
    """Unpack Anomaly DBs if needed; does not rediscover GAMMA/Anomaly pack lists."""
    from .db_unpack import ensure_anomaly_db_unpacked

    data = settings if settings is not None else default_settings()
    anomaly = str(data.get("anomaly_root") or "").strip()
    ensure_anomaly_db_unpacked(anomaly_root=anomaly or None)
    _merge_anomaly_unpack_paths(data)
    return data


def _merge_anomaly_unpack_paths(settings: dict) -> None:
    """If tools/_unpacked appeared after unpack, prepend those dirs (lowest priority)."""
    anomaly = _norm_root(settings.get("anomaly_root", ""))
    if not anomaly:
        return
    root = Path(anomaly)
    extras = {
        "gamedata_texture_roots": [root / "tools" / "_unpacked" / "textures"],
        "gamedata_descr_roots": [
            root / "tools" / "_unpacked" / "configs" / "ui" / "textures_descr"
        ],
        "gamedata_text_roots": [
            root / "tools" / "_unpacked" / "configs" / "text" / "eng"
        ],
    }
    for key, paths in extras.items():
        cur = list(settings.get(key) or [])
        seen = {str(Path(p)).lower() for p in cur}
        prepend: list[str] = []
        for p in paths:
            if not p.is_dir():
                continue
            try:
                key_path = str(p.resolve())
            except OSError:
                key_path = str(p)
            if key_path.lower() in seen:
                continue
            prepend.append(key_path)
            seen.add(key_path.lower())
        if prepend:
            # Unpacked Anomaly is base layer — must stay before GAMMA / custom.
            settings[key] = prepend + cur


# Main-window splitter: left sidebar, editor, right sidebar.
DEFAULT_SIDEBAR_WIDTH = 300
# Absolute minimum sidebar width (widget + splitter + persisted sizes).
SIDEBAR_MIN_WIDTH = 200
# Prior shipped defaults — migrate equal pairs to the current default.
_LEGACY_SIDEBAR_WIDTHS = (350, 525)
DEFAULT_SPLITTER_SIZES = [
    DEFAULT_SIDEBAR_WIDTH,
    700,
    DEFAULT_SIDEBAR_WIDTH,
]


def normalize_splitter_sizes(raw: object) -> list[int]:
    """Return [left, mid, right] px; repair invalid / crushed / legacy defaults."""
    if isinstance(raw, (list, tuple)) and len(raw) == 3:
        try:
            left, mid, right = (int(x) for x in raw)
        except (TypeError, ValueError):
            return list(DEFAULT_SPLITTER_SIZES)
        mid = max(100, mid)
        # Old equal-sidebar defaults → current default (keep mid).
        if left == right and left in _LEGACY_SIDEBAR_WIDTHS:
            return [DEFAULT_SIDEBAR_WIDTH, mid, DEFAULT_SIDEBAR_WIDTH]
        # Below absolute minimum — restore sidebar defaults.
        if left < SIDEBAR_MIN_WIDTH or right < SIDEBAR_MIN_WIDTH:
            return [DEFAULT_SIDEBAR_WIDTH, mid, DEFAULT_SIDEBAR_WIDTH]
        return [max(SIDEBAR_MIN_WIDTH, left), mid, max(SIDEBAR_MIN_WIDTH, right)]
    return list(DEFAULT_SPLITTER_SIZES)


def default_settings() -> dict:
    # Roots stay empty until the user sets them (setup / settings).
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
        "show_rulers": True,
        "last_file_dir": "",
        "last_texture_dir": "",
        "last_deploy_dir": "",
        # source XML path → deploy overwrite target (resolved strings).
        "deploy_targets": {},
        "recent_files": [],
        "splitter_sizes": list(DEFAULT_SPLITTER_SIZES),
        # 0 = auto from Anomaly appdata/user.ltx vid_mode (else 1080).
        "font_device_height": 0,
        "window": {
            "x": None,
            "y": None,
            "width": 1400,
            "height": 900,
            "maximized": False,
        },
    }
    data.update(_empty_asset_roots())
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


def _has_scanned_asset_roots(settings: dict) -> bool:
    for key in (
        "gamedata_texture_roots",
        "gamedata_descr_roots",
        "gamedata_text_roots",
    ):
        val = settings.get(key)
        if isinstance(val, list) and any(str(x).strip() for x in val):
            return True
    return False


def _path_key(path: object) -> str:
    try:
        return str(Path(str(path)).expanduser().resolve()).lower()
    except OSError:
        return str(path).strip().lower().replace("/", "\\")


def _legacy_whole_tree_scan_keys(settings: dict) -> set[str]:
    """Paths that must never be recursive scan roots (whole GAMMA trees)."""
    gamma = _norm_root(settings.get("gamma_root", ""))
    if not gamma:
        return set()
    root = Path(gamma)
    return {
        _path_key(root / "mods"),
        _path_key(
            root / ".Grok's Modpack Installer" / "G.A.M.M.A" / "modpack_addons"
        ),
    }


def uses_legacy_whole_tree_scans(settings: dict) -> bool:
    """True if texture/text scan roots still point at entire GAMMA mods trees."""
    banned = _legacy_whole_tree_scan_keys(settings)
    if not banned:
        return False
    for key in ("texture_roots", "textures_descr_roots", "text_roots"):
        for raw in settings.get(key) or []:
            if _path_key(raw) in banned:
                return True
    return False


def migrate_legacy_scan_roots(settings: dict) -> bool:
    """Strip whole-tree GAMMA scans and rediscover pack folders once. Returns if changed."""
    had_legacy = uses_legacy_whole_tree_scans(settings)
    banned = _legacy_whole_tree_scan_keys(settings)
    changed = False
    if banned:
        for key in ("texture_roots", "textures_descr_roots", "text_roots"):
            old = list(settings.get(key) or [])
            new = [p for p in old if _path_key(p) not in banned]
            if new != old:
                settings[key] = new
                changed = True

    if had_legacy and installs_configured(settings):
        rescan_asset_roots(settings)
        return True
    if installs_configured(settings) and not _has_scanned_asset_roots(settings):
        rescan_asset_roots(settings)
        return True
    # Older rescans saved every mod's text/eng (hundreds of folders). Keep UI + text packs.
    text_n = len(settings.get("gamedata_text_roots") or [])
    if installs_configured(settings) and text_n > 25:
        rescan_asset_roots(settings)
        settings["asset_roots_version"] = ASSET_ROOTS_VERSION
        return True
    # Discovery / override order changed (MO2 modlist, Anomaly base order).
    if installs_configured(settings) and int(
        settings.get("asset_roots_version") or 0
    ) < ASSET_ROOTS_VERSION:
        rescan_asset_roots(settings)
        settings["asset_roots_version"] = ASSET_ROOTS_VERSION
        return True

    return changed


def load_settings() -> dict:
    data = default_settings()
    if SETTINGS_PATH.is_file():
        try:
            loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                for key, value in loaded.items():
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
    data["font_device_height"] = clamp_font_device_height(
        data.get("font_device_height", 0)
    )
    data["show_element_labels"] = bool(data.get("show_element_labels", False))
    # Session-only preview; never persist (drop legacy key if present).
    data.pop("preview_aspect", None)
    data["recent_files"] = normalize_recent_files(data.get("recent_files"))
    data["deploy_targets"] = normalize_deploy_targets(data.get("deploy_targets"))
    data["last_deploy_dir"] = _norm_root(data.get("last_deploy_dir", ""))
    if data["last_deploy_dir"] and not Path(data["last_deploy_dir"]).is_dir():
        data["last_deploy_dir"] = ""
    data["custom_roots"] = normalize_custom_roots(data.get("custom_roots"))
    data["anomaly_root"] = _norm_root(data.get("anomaly_root", ""))
    data["gamma_root"] = _norm_root(data.get("gamma_root", ""))
    prev_split = data.get("splitter_sizes")
    data["splitter_sizes"] = normalize_splitter_sizes(prev_split)
    split_repaired = data["splitter_sizes"] != prev_split

    # Drop legacy whole-tree GAMMA scans (was making every launch ~8s+).
    if migrate_legacy_scan_roots(data) or split_repaired:
        try:
            save_settings(data)
        except OSError:
            pass
    elif not any(data.get(k) for k in _ASSET_ROOT_KEYS):
        data.update(_empty_asset_roots())
    return data


def save_settings(data: dict) -> None:
    """Persist settings as-is (does not rediscover asset roots)."""
    data["anomaly_root"] = _norm_root(data.get("anomaly_root", ""))
    data["gamma_root"] = _norm_root(data.get("gamma_root", ""))
    data["custom_roots"] = normalize_custom_roots(data.get("custom_roots"))
    data.pop("preview_aspect", None)
    SETTINGS_PATH.write_text(
        json.dumps(data, indent=2) + "\n",
        encoding="utf-8",
    )

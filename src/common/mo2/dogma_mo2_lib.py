#!/usr/bin/env python3
"""DOGMA MO2 shared library — deps, disable, defaults, validate report, paths."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator


SEPARATOR_NAME = "DOGMA DEPENDENCIES_separator"
ACTION_LOG_NAME = "dogma_mo2.log"
REPORT_LOG_NAME = "dogma_mo2_report.log"
FINGERPRINT_NAME = "defaults_fingerprint.json"
USER_URL_PREFIX = "dogma:user:"
MANAGED_FOLDER_PREFIX = "DOGMA - "


# ---------------------------------------------------------------------------
# console / log
# ---------------------------------------------------------------------------

def _use_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            return False
    return sys.stdout.isatty()


_COLOR = _use_color()


def info(msg: str) -> None:
    print(msg)


def ok(msg: str) -> None:
    print(f"\033[32m{msg}\033[0m" if _COLOR else msg)


def warn(msg: str) -> None:
    print(f"\033[33m{msg}\033[0m" if _COLOR else msg)


def err(msg: str) -> None:
    print(f"\033[31m{msg}\033[0m" if _COLOR else msg, file=sys.stderr)


def append_action_log(mo2_dir: Path, line: str) -> None:
    path = mo2_dir / ACTION_LOG_NAME
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"[{stamp}] {line}\n")


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------

def script_dir() -> Path:
    return Path(__file__).resolve().parent


def resolve_mo2_root(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit)
    # When registered as MO2 executable, cwd is instance root.
    cwd = Path.cwd()
    if (cwd / "ModOrganizer.exe").is_file():
        return cwd
    # Deployed under mods/DOGMA/mo2/
    parent = script_dir().parent.parent  # mods/
    candidate = parent.parent  # MO2 root
    if (candidate / "ModOrganizer.exe").is_file():
        return candidate
    default = Path(r"C:\GAMMA") if sys.platform == "win32" else Path(os.environ.get("MO2_ROOT", r"C:\GAMMA"))
    return default


def resolve_config_dir(mo2_root: Path, override: Path | None = None) -> Path:
    if override and override.is_dir():
        return override
    staged = script_dir() / "config"
    if staged.is_dir() and (staged / "manifest.yml").is_file():
        return staged
    # Author / repo: src/common/mo2 → ../../../config
    repo_cfg = script_dir().parent.parent.parent / "config"
    if (repo_cfg / "manifest.yml").is_file():
        return repo_cfg
    dogma = mo2_root / "mods" / "DOGMA" / "mo2" / "config"
    if dogma.is_dir() and (dogma / "manifest.yml").is_file():
        return dogma
    return staged


def resolve_manifest_path(cfg_dir: Path) -> Path:
    yml = cfg_dir / "manifest.yml"
    if yml.is_file():
        return yml
    raise FileNotFoundError(f"manifest.yml not found under {cfg_dir}")


def mo2_tools_dir(mo2_root: Path) -> Path:
    return mo2_root / "mods" / "DOGMA" / "mo2"


def mo2_running() -> bool:
    if sys.platform == "win32":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq ModOrganizer.exe", "/NH"],
                capture_output=True,
                text=True,
                check=False,
            )
            return "ModOrganizer.exe" in (out.stdout or "")
        except OSError:
            return False
    try:
        out = subprocess.run(["pgrep", "-x", "ModOrganizer"], capture_output=True, check=False)
        return out.returncode == 0
    except OSError:
        return False


def read_mo2_ini_value(ini_path: Path, key: str) -> str:
    if not ini_path.is_file():
        raise FileNotFoundError(f"ModOrganizer.ini not found: {ini_path}")
    prefix = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for raw in ini_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not prefix.match(raw):
            continue
        m = re.search(r"@ByteArray\((.+)\)\s*$", raw)
        if m:
            value = m.group(1)
        else:
            value = raw.split("=", 1)[1].strip()
        return value.replace("\\\\", "\\")
    raise ValueError(f"{key} not found in {ini_path}")


def selected_profile(mo2_root: Path, profile: str = "") -> str:
    if profile:
        return profile
    return read_mo2_ini_value(mo2_root / "ModOrganizer.ini", "selected_profile")


def modlist_path(mo2_root: Path, profile: str = "") -> Path:
    name = selected_profile(mo2_root, profile)
    path = mo2_root / "profiles" / name / "modlist.txt"
    if not path.is_file():
        raise FileNotFoundError(f"modlist.txt not found: {path}")
    return path


def read_text_lines(path: Path) -> list[str]:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = raw.decode("utf-8", errors="replace")
    if text.endswith("\n"):
        text = text[:-1]
        if text.endswith("\r"):
            text = text[:-1]
    if not text:
        return []
    return text.splitlines()


def write_text_lines(path: Path, lines: Iterable[str]) -> None:
    data = "\r\n".join(lines) + "\r\n"
    path.write_bytes(data.encode("utf-8"))


def stamp_backup(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = Path(f"{path}.bak.{stamp}")
    shutil.copy2(path, backup)
    return backup


def find_7z() -> Path | None:
    for name in ("7z", "7za", "7z.exe", "7za.exe"):
        found = shutil.which(name)
        if found:
            return Path(found)
    for candidate in (
        Path(r"C:\Program Files\7-Zip\7z.exe"),
        Path(r"C:\Program Files (x86)\7-Zip\7z.exe"),
        Path(r"C:\GAMMA\.Grok's Modpack Installer\7zip\7z.exe"),
    ):
        if candidate.is_file():
            return candidate
    return None


def pyyaml_ok() -> bool:
    try:
        import yaml  # noqa: F401

        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# disable / defaults / unified manifest.yml
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Rule:
    kind: str  # exact | substring
    pattern: str
    features: tuple[str, ...] = ()
    source: str = ""  # feature path or dep:<id>

    def label(self) -> str:
        base = (
            f"substring:{self.pattern}"
            if self.kind == "substring"
            else self.pattern
        )
        tag = self.source or (", ".join(self.features) if self.features else "")
        return f"{base} [{tag}]" if tag else base


@dataclass(frozen=True)
class InitSetting:
    mod_pattern: str
    axr_section: str
    key: str
    value: str


def _parse_disable_name(raw: str) -> tuple[str, str] | None:
    key = str(raw).strip()
    if not key or key.startswith("#"):
        return None
    low = key.lower()
    # Default: exact MO2 folder / modlist name. Opt-in partial: substring:… / contains:…
    if low.startswith("substring:") or low.startswith("contains:"):
        return ("substring", key.split(":", 1)[1].strip())
    if low.startswith("exact:"):
        return ("exact", key.split(":", 1)[1].strip())
    return ("exact", key)


def _settings_from_mapping(
    mod_pattern: str,
    mapping: dict,
) -> list[InitSetting]:
    out: list[InitSetting] = []
    if not isinstance(mapping, dict):
        return out
    for key, val in mapping.items():
        key_s = str(key).strip()
        val_s = str(val).strip()
        if not key_s:
            continue
        axr_section = "mcm"
        if key_s.startswith("@"):
            rest = key_s[1:]
            if "/" not in rest:
                raise ValueError(f"defaults @{mod_pattern}: want @Section/key (got {key_s})")
            axr_section, key_s = rest.split("/", 1)
            axr_section, key_s = axr_section.strip(), key_s.strip()
        out.append(InitSetting(mod_pattern, axr_section, key_s, val_s))
    return out


LEVEL_RANK = {"off": 0, "dev": 1, "release": 2}
LEVEL_ALIASES = {
    "0": "off",
    "1": "dev",
    "2": "release",
    "off": "off",
    "dev": "dev",
    "release": "release",
    "local": "dev",
}


def parse_level(raw) -> str:
    if raw is False:
        return "off"
    if raw is True:
        raise ValueError("level must be off|dev|release (got boolean true — quote strings in YAML)")
    key = str(raw).strip().lower()
    if key not in LEVEL_ALIASES:
        raise ValueError(f"level must be off|dev|release (got {raw!r})")
    return LEVEL_ALIASES[key]


def level_meets(level: str, minimum: str) -> bool:
    return LEVEL_RANK[parse_level(level)] >= LEVEL_RANK[parse_level(minimum)]


@dataclass
class Dependency:
    id: str
    label: str
    tier: str  # required (from a feature) | suggested
    url: str = ""
    source: str = "auto"  # auto | user
    file: str = ""
    howto: str = ""
    disable: list[str] = field(default_factory=list)
    enable: list[str] = field(default_factory=list)
    defaults: dict[str, str] = field(default_factory=dict)
    target_mod: str = ""
    after_unpack: str = ""
    feature: str = ""  # owning feature path when from features.*.requirements


@dataclass
class FeatureMeta:
    path: str
    level: str  # off | dev | release
    disable: list[str] = field(default_factory=list)
    enable: list[str] = field(default_factory=list)
    defaults: dict[str, str] = field(default_factory=dict)
    target_mod: str = ""
    requirements: list[Dependency] = field(default_factory=list)

    @property
    def always_on(self) -> bool:
        return self.path.lower() == "common"


def dogma_mod_dir(mo2_root: Path) -> Path:
    return mo2_root / "mods" / "DOGMA"


def feature_path_key(feat: str) -> str:
    """Build script/config stem used by tools/build.sh (category_feature)."""
    return feat.strip().replace("\\", "/").replace("/", "_").lower()


def detect_installed_features(
    mo2_root: Path,
    known: Iterable[str],
) -> set[str]:
    """Features present under mods/DOGMA (FOMOD may install a subset).

    Detects build output markers: dogma_<cat>_<feat>_*, ui_mcm_dogma_*, etc.
    ``common`` is always treated as installed when the DOGMA mod folder exists.
    """
    known_list = [str(f).strip() for f in known if str(f).strip()]
    root = dogma_mod_dir(mo2_root)
    if not root.is_dir():
        return set()

    names: list[str] = []
    gamedata = root / "gamedata"
    if gamedata.is_dir():
        for p in gamedata.rglob("*"):
            if p.is_file():
                names.append(p.name.lower())
    # Also scan mo2/ for feature-specific tools (e.g. sound_prefetch)
    mo2_tools = root / "mo2"
    if mo2_tools.is_dir():
        for p in mo2_tools.rglob("*"):
            if p.is_file():
                names.append(p.name.lower())

    blob = "\n".join(names)
    installed: set[str] = set()
    for feat in known_list:
        if feat.lower() == "common":
            installed.add(feat)
            continue
        key = feature_path_key(feat)
        markers = (
            f"dogma_{key}_",
            f"dogma_{key}.",
            f"zzzz_dogma_{key}_",
            f"modxml_dogma_{key}_",
            f"ui_mcm_dogma_{key}",
            f"st_dogma_{key}",
            f"mod_system_dogma_{key}",
            f"mod_materials_dogma_{key}",
        )
        if any(m in blob for m in markers) or f"dogma_{key}" in blob:
            installed.add(feat)
    return installed


def _feature_is_active(
    meta: FeatureMeta,
    min_level: str = "dev",
    *,
    installed: set[str] | None = None,
) -> bool:
    """Active = manifest level OK, and (if given) feature is installed in MO2."""
    if meta.always_on:
        return True
    if not level_meets(meta.level, min_level):
        return False
    if installed is not None:
        # Match by exact path; also accept case-insensitive
        if meta.path in installed:
            return True
        low = {x.lower() for x in installed}
        return meta.path.lower() in low
    return True


def resolve_installed_features(
    mo2_root: Path | None,
    data: ManifestData,
) -> set[str] | None:
    """None = do not gate on install (legacy / no MO2 root). Else detected set."""
    if mo2_root is None:
        return None
    return detect_installed_features(mo2_root, data.features.keys())


@dataclass
class ManifestData:
    path: Path
    features: dict[str, FeatureMeta] = field(default_factory=dict)
    suggested: list[Dependency] = field(default_factory=list)
    defaults: dict[str, list[InitSetting]] = field(default_factory=dict)

    def feature_requirements(
        self,
        min_level: str | int = "dev",
        *,
        installed: set[str] | None = None,
    ) -> list[Dependency]:
        """Requirements from common + active installed features (dedupe by id)."""
        min_level = parse_level(min_level)
        seen: set[str] = set()
        out: list[Dependency] = []
        for feat in ("common", *sorted(k for k in self.features if k != "common")):
            meta = self.features.get(feat)
            if not meta or not _feature_is_active(
                meta, min_level, installed=installed
            ):
                continue
            for dep in meta.requirements:
                if dep.id in seen:
                    continue
                seen.add(dep.id)
                out.append(dep)
        return out

    def collect_defaults(
        self,
        *,
        min_level: str | int = "dev",
        installed: set[str] | None = None,
        include_suggested: bool = True,
    ) -> dict[str, list[InitSetting]]:
        """Defaults for active/installed features (+ optional suggested entries)."""
        min_level = parse_level(min_level)
        defaults: dict[str, list[InitSetting]] = {}
        for feat, meta in self.features.items():
            if not _feature_is_active(meta, min_level, installed=installed):
                continue
            if meta.defaults:
                pattern = meta.target_mod or meta.path
                defaults.setdefault(pattern, []).extend(
                    _settings_from_mapping(pattern, meta.defaults)
                )
            for dep in meta.requirements:
                if not dep.defaults:
                    continue
                pattern = dep.target_mod or dep.label or dep.id
                defaults.setdefault(pattern, []).extend(
                    _settings_from_mapping(pattern, dep.defaults)
                )
        if include_suggested:
            for dep in self.suggested:
                if not dep.defaults:
                    continue
                pattern = dep.target_mod or dep.label or dep.id
                defaults.setdefault(pattern, []).extend(
                    _settings_from_mapping(pattern, dep.defaults)
                )
        return defaults

    @property
    def requirements(self) -> list[Dependency]:
        """All feature requirements at dev+ (compat alias)."""
        return self.feature_requirements("dev")

    @property
    def mods(self) -> list[Dependency]:
        return self.feature_requirements("dev") + list(self.suggested)


def _dep_from_mapping(
    dep_id: str,
    item: dict,
    *,
    tier: str,
    section: str,
    feature: str = "",
) -> Dependency:
    disable = item.get("disable") or []
    enable = item.get("enable") or []
    defaults = item.get("defaults") or {}
    if not isinstance(disable, list):
        raise ValueError(f"{section}.{dep_id}.disable must be a list")
    if not isinstance(enable, list):
        raise ValueError(f"{section}.{dep_id}.enable must be a list")
    if defaults and not isinstance(defaults, dict):
        raise ValueError(f"{section}.{dep_id}.defaults must be a mapping")
    return Dependency(
        id=dep_id,
        label=str(item.get("label") or dep_id),
        tier=tier,
        url=str(item.get("url") or "").strip(),
        source=str(item.get("source") or "auto").strip().lower(),
        file=str(item.get("file") or "").strip(),
        howto=str(item.get("howto") or "").strip(),
        disable=[str(x) for x in disable],
        enable=[str(x) for x in enable],
        defaults={str(k): str(v) for k, v in (defaults or {}).items()},
        target_mod=str(item.get("target_mod") or "").strip(),
        after_unpack=str(item.get("after_unpack") or "").strip(),
        feature=feature,
    )


def _parse_external_list(
    raw_list,
    *,
    tier: str,
    section: str,
    feature: str = "",
) -> list[Dependency]:
    """Parse a YAML list of {id, ...} maps (used by features.*.requirements)."""
    out: list[Dependency] = []
    if not raw_list:
        return out
    if not isinstance(raw_list, list):
        raise ValueError(f"manifest.yml {section}: must be a list")
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        dep_id = str(item.get("id") or "").strip()
        if not dep_id:
            raise ValueError(f"{section} entry missing id")
        out.append(
            _dep_from_mapping(
                dep_id, item, tier=tier, section=section, feature=feature
            )
        )
    return out


def _parse_external_map(
    raw_map,
    *,
    tier: str,
    section: str,
) -> list[Dependency]:
    """Parse a YAML mapping keyed by id (suggested: — same shape as features:)."""
    out: list[Dependency] = []
    if not raw_map:
        return out
    # Legacy list form: [{id: ...}, ...]
    if isinstance(raw_map, list):
        return _parse_external_list(raw_map, tier=tier, section=section)
    if not isinstance(raw_map, dict):
        raise ValueError(f"manifest.yml {section}: must be a mapping (keyed by id)")
    for key, meta in raw_map.items():
        dep_id = str(key).strip()
        if not dep_id:
            continue
        if meta is None:
            meta = {}
        if not isinstance(meta, dict):
            raise ValueError(f"{section}.{dep_id}: want a mapping")
        # Allow optional redundant id: inside the map; key wins.
        if "id" in meta and str(meta.get("id") or "").strip() not in ("", dep_id):
            raise ValueError(
                f"{section}.{dep_id}: id field {meta.get('id')!r} must match key"
            )
        out.append(
            _dep_from_mapping(dep_id, meta, tier=tier, section=section)
        )
    return out


def load_manifest(path: Path) -> ManifestData:
    if not path.is_file():
        raise FileNotFoundError(f"manifest.yml not found: {path}")
    if not pyyaml_ok():
        raise RuntimeError("PyYAML not installed. Run DOGMA (Setup Tools).bat first.")
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("manifest.yml root must be a mapping")

    if raw.get("requirements") is not None:
        raise ValueError(
            "manifest.yml top-level 'requirements:' removed — put required packs under "
            "features.<name>.requirements (use features.common for always-on)"
        )
    if raw.get("defaults"):
        raise ValueError(
            "manifest.yml top-level 'defaults:' removed — put defaults under each "
            "feature / feature.requirements[] / suggested.<id> entry (optional target_mod:)"
        )

    features: dict[str, FeatureMeta] = {}
    feat_block = raw.get("features") or {}
    if not isinstance(feat_block, dict):
        raise ValueError("manifest.yml features: must be a mapping")
    for feat_path, meta in feat_block.items():
        fp = str(feat_path).strip()
        if not fp:
            continue
        is_common = fp.lower() == "common"
        reqs: list[Dependency] = []
        # Bare YAML `off` becomes False — treat as level off.
        if meta is False:
            level = "off"
            disable, enable, defaults, target_mod = [], [], {}, ""
        elif isinstance(meta, (int, str)) and not isinstance(meta, bool):
            level = parse_level(meta)
            disable, enable, defaults, target_mod = [], [], {}, ""
        elif isinstance(meta, dict):
            if is_common:
                level = parse_level(meta.get("level", "release"))
            elif "level" not in meta:
                raise ValueError(f"features.{fp}: missing level (off|dev|release)")
            else:
                level = parse_level(meta["level"])
            disable = meta.get("disable") or []
            enable = meta.get("enable") or []
            defaults = meta.get("defaults") or {}
            target_mod = str(meta.get("target_mod") or "").strip()
            if not isinstance(disable, list):
                raise ValueError(f"features.{fp}.disable must be a list")
            if not isinstance(enable, list):
                raise ValueError(f"features.{fp}.enable must be a list")
            if defaults and not isinstance(defaults, dict):
                raise ValueError(f"features.{fp}.defaults must be a mapping")
            reqs = _parse_external_list(
                meta.get("requirements"),
                tier="required",
                section=f"features.{fp}.requirements",
                feature=fp,
            )
        else:
            raise ValueError(f"features.{fp}: want level or mapping")
        features[fp] = FeatureMeta(
            path=fp,
            level=level,
            disable=[str(x) for x in disable],
            enable=[str(x) for x in enable],
            defaults={str(k): str(v) for k, v in (defaults or {}).items()},
            target_mod=target_mod,
            requirements=reqs,
        )

    suggested = _parse_external_map(
        raw.get("suggested"), tier="suggested", section="suggested"
    )
    # Legacy top-level mods: with tier
    if not suggested and (raw.get("mods") or raw.get("dependencies")):
        for item in raw.get("mods") or raw.get("dependencies") or []:
            if not isinstance(item, dict):
                continue
            tier = str(item.get("tier") or "suggested").strip().lower()
            if tier == "required":
                raise ValueError(
                    "legacy mods with tier:required — move under features.*.requirements"
                )
            suggested.extend(
                _parse_external_list([item], tier="suggested", section="mods")
            )

    defaults: dict[str, list[InitSetting]] = {}
    for feat in features.values():
        if feat.defaults:
            pattern = feat.target_mod or feat.path
            defaults.setdefault(pattern, []).extend(
                _settings_from_mapping(pattern, feat.defaults)
            )
        for dep in feat.requirements:
            if not dep.defaults:
                continue
            pattern = dep.target_mod or dep.label or dep.id
            defaults.setdefault(pattern, []).extend(
                _settings_from_mapping(pattern, dep.defaults)
            )

    for dep in suggested:
        if not dep.defaults:
            continue
        pattern = dep.target_mod or dep.label or dep.id
        defaults.setdefault(pattern, []).extend(
            _settings_from_mapping(pattern, dep.defaults)
        )

    return ManifestData(
        path=path,
        features=features,
        suggested=suggested,
        defaults=defaults,
    )


def read_manifest_levels(path: Path) -> dict[str, int]:
    """Feature path → numeric rank (0/1/2). Accepts manifest.yml (or legacy .ini)."""
    if path.suffix.lower() in (".yml", ".yaml") or path.name == "manifest.yml":
        data = load_manifest(path)
        return {k.lower(): LEVEL_RANK[v.level] for k, v in data.features.items()}
    if not path.is_file():
        raise FileNotFoundError(f"Manifest not found: {path}")
    levels: dict[str, int] = {}
    section: str | None = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if section != "features" or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.split(";", 1)[0].strip()
        if not key or key == "common":
            continue
        levels[key.lower()] = LEVEL_RANK[parse_level(val)]
    return levels


def feature_disable_rules(
    data: ManifestData,
    *,
    min_level: str | int = "dev",
    installed: set[str] | None = None,
) -> tuple[list[Rule], list[str], list[str]]:
    min_level = parse_level(min_level)
    merged: dict[tuple[str, str], list[str]] = {}
    patterns: dict[tuple[str, str], str] = {}
    active: list[str] = []
    skipped: list[str] = []

    for feat, meta in data.features.items():
        if not _feature_is_active(meta, min_level, installed=installed):
            if meta.disable:
                skipped.append(feat)
            continue
        if not meta.disable:
            continue
        active.append(feat)
        for raw in meta.disable:
            parsed = _parse_disable_name(raw)
            if not parsed:
                continue
            kind, pattern = parsed
            key = (kind, pattern.lower())
            patterns.setdefault(key, pattern)
            merged.setdefault(key, [])
            if feat not in merged[key]:
                merged[key].append(feat)

    rules = [
        Rule(
            kind=kind,
            pattern=patterns[(kind, pat)],
            features=tuple(feats),
            source=feats[0] if len(feats) == 1 else "; ".join(feats),
        )
        for (kind, pat), feats in merged.items()
    ]
    return rules, active, skipped


def feature_enable_rules(
    data: ManifestData,
    *,
    min_level: str | int = "dev",
    installed: set[str] | None = None,
) -> tuple[list[Rule], list[str]]:
    """Rules for mods that should be enabled when the feature (or common) is active."""
    min_level = parse_level(min_level)
    merged: dict[tuple[str, str], list[str]] = {}
    patterns: dict[tuple[str, str], str] = {}
    active: list[str] = []

    for feat, meta in data.features.items():
        if not _feature_is_active(meta, min_level, installed=installed):
            continue
        if not meta.enable:
            continue
        active.append(feat)
        for raw in meta.enable:
            parsed = _parse_disable_name(raw)
            if not parsed:
                continue
            kind, pattern = parsed
            key = (kind, pattern.lower())
            patterns.setdefault(key, pattern)
            merged.setdefault(key, [])
            if feat not in merged[key]:
                merged[key].append(feat)

    rules = [
        Rule(
            kind=kind,
            pattern=patterns[(kind, pat)],
            features=tuple(feats),
            source=feats[0] if len(feats) == 1 else "; ".join(feats),
        )
        for (kind, pat), feats in merged.items()
    ]
    return rules, active


def update_modlist_enable_by_rules(
    modlist: Path,
    rules: list[Rule],
    dry_run: bool,
) -> ModlistResult:
    """Enable disabled (+/-) mods that match rules. Result.enabled = newly enabled."""
    if not rules:
        return ModlistResult()
    lines_in = read_text_lines(modlist)
    out: list[str] = []
    result = ModlistResult()
    matched: set[str] = set()

    for line in lines_in:
        m = re.match(r"^([+\-])(.+)$", line)
        if m:
            flag, name = m.group(1), m.group(2)
            rule = mod_matches(name, rules)
            if rule:
                matched.add(name)
                if flag == "-":
                    result.enabled.append(name)
                    out.append(f"+{name}")
                    continue
                result.already.append(name)
        out.append(line)

    result.unmatched = [
        r.label() for r in rules if not any(mod_matches(n, [r]) for n in matched)
    ]
    if result.enabled and not dry_run:
        stamp_backup(modlist)
        write_text_lines(modlist, out)
    return result


def read_disable_ini(
    path: Path,
    manifest_path: Path,
    *,
    min_level: str | int = "dev",
) -> tuple[list[Rule], list[str], list[str]]:
    """Legacy name: prefer unified manifest.yml when manifest_path is .yml."""
    if manifest_path.suffix.lower() in (".yml", ".yaml"):
        return feature_disable_rules(load_manifest(manifest_path), min_level=min_level)
    # Fallback: old dual-ini path
    if not path.is_file():
        raise FileNotFoundError(f"disabled.ini not found: {path}")

    min_rank = LEVEL_RANK[parse_level(min_level)]
    levels = read_manifest_levels(manifest_path)
    by_feature: dict[str, list[tuple[str, str]]] = {}
    section: str | None = None

    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            if not section:
                raise ValueError("disabled.ini has an empty [section]")
            by_feature.setdefault(section, [])
            continue
        if section is None:
            raise ValueError(f"disabled.ini entry outside a [feature] section: {raw.strip()}")
        key = line.split(";", 1)[0].strip()
        if "=" in key:
            name, val = key.split("=", 1)
            name, val = name.strip(), val.strip()
            if val == "0":
                continue
            if val not in ("", "1"):
                raise ValueError(f"disabled.ini entry must be a mod name (got: {raw.strip()})")
            key = name
        if not key:
            continue
        parsed = _parse_disable_name(key)
        if parsed:
            by_feature[section].append(parsed)

    merged: dict[tuple[str, str], list[str]] = {}
    patterns: dict[tuple[str, str], str] = {}
    active: list[str] = []
    skipped: list[str] = []

    for feature, entries in by_feature.items():
        level = levels.get(feature.lower(), 0)
        if level < min_rank:
            skipped.append(feature)
            continue
        active.append(feature)
        for kind, pattern in entries:
            key = (kind, pattern.lower())
            patterns.setdefault(key, pattern)
            merged.setdefault(key, [])
            if feature not in merged[key]:
                merged[key].append(feature)

    rules = [
        Rule(
            kind=kind,
            pattern=patterns[(kind, pat)],
            features=tuple(feats),
            source=feats[0] if len(feats) == 1 else "; ".join(feats),
        )
        for (kind, pat), feats in merged.items()
    ]
    return rules, active, skipped


def rules_from_disable_names(names: Iterable[str], source: str = "") -> list[Rule]:
    rules: list[Rule] = []
    for raw in names:
        parsed = _parse_disable_name(raw)
        if not parsed:
            continue
        kind, pattern = parsed
        rules.append(Rule(kind, pattern, source=source))
    return rules


def mod_matches(name: str, rules: Iterable[Rule]) -> Rule | None:
    lower = name.lower()
    for rule in rules:
        if rule.kind == "exact":
            if name.lower() == rule.pattern.lower():
                return rule
        elif rule.pattern.lower() in lower:
            return rule
    return None


@dataclass
class ModlistResult:
    disabled: list[str] = field(default_factory=list)
    already: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    enabled: list[str] = field(default_factory=list)


def parse_modlist(modlist: Path) -> list[tuple[str, str]]:
    """Return list of (flag, name) for +/- lines; other lines as ('', raw)."""
    rows: list[tuple[str, str]] = []
    for line in read_text_lines(modlist):
        m = re.match(r"^([+\-])(.+)$", line)
        if m:
            rows.append((m.group(1), m.group(2)))
        else:
            rows.append(("", line))
    return rows


def list_modlist_entries(modlist: Path) -> list[tuple[str, str]]:
    return [(f, n) for f, n in parse_modlist(modlist) if f]


def list_modlist_names(modlist_path: Path) -> list[str]:
    return [n for f, n in list_modlist_entries(modlist_path)]


def update_modlist_disable(modlist: Path, rules: list[Rule], dry_run: bool) -> ModlistResult:
    lines_in = read_text_lines(modlist)
    out: list[str] = []
    result = ModlistResult()
    matched: set[str] = set()

    for line in lines_in:
        m = re.match(r"^([+\-])(.+)$", line)
        if m:
            flag, name = m.group(1), m.group(2)
            rule = mod_matches(name, rules)
            if rule:
                matched.add(name)
                if flag == "+":
                    result.disabled.append(name)
                    out.append(f"-{name}")
                    continue
                result.already.append(name)
        out.append(line)

    result.unmatched = [r.label() for r in rules if not any(mod_matches(n, [r]) for n in matched)]
    if result.disabled and not dry_run:
        stamp_backup(modlist)
        write_text_lines(modlist, out)
    return result


def enable_mods_in_modlist(modlist: Path, names: Iterable[str], dry_run: bool) -> list[str]:
    want = {n.lower() for n in names}
    lines = read_text_lines(modlist)
    out: list[str] = []
    enabled: list[str] = []
    for line in lines:
        m = re.match(r"^([+\-])(.+)$", line)
        if m:
            flag, name = m.group(1), m.group(2)
            if name.lower() in want and flag == "-":
                enabled.append(name)
                out.append(f"+{name}")
                continue
        out.append(line)
    if enabled and not dry_run:
        stamp_backup(modlist)
        write_text_lines(modlist, out)
    return enabled


def read_initialize_ini(path: Path) -> dict[str, list[InitSetting]]:
    """Load defaults from manifest.yml (or legacy defaults.ini)."""
    if path.suffix.lower() in (".yml", ".yaml") or path.name == "manifest.yml":
        return load_manifest(path).defaults
    if not path.is_file():
        raise FileNotFoundError(f"defaults not found: {path}")
    by_mod: dict[str, list[InitSetting]] = {}
    section: str | None = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            if not section:
                raise ValueError("defaults.ini has an empty [section]")
            by_mod.setdefault(section, [])
            continue
        if section is None:
            raise ValueError(f"defaults.ini entry outside a [mod] section: {raw.strip()}")
        if "=" not in line:
            raise ValueError(f"defaults.ini entry must be key = value (got: {raw.strip()})")
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.split(";", 1)[0].strip()
        if not key:
            raise ValueError(f"defaults.ini entry missing key: {raw.strip()}")
        axr_section = "mcm"
        if key.startswith("@"):
            rest = key[1:]
            if "/" not in rest:
                raise ValueError(f"defaults.ini @entry must be @Section/key (got: {raw.strip()})")
            axr_section, key = rest.split("/", 1)
            axr_section, key = axr_section.strip(), key.strip()
        by_mod[section].append(InitSetting(section, axr_section, key, val))
    return by_mod


def find_present_mod(pattern: str, mod_names: Iterable[str]) -> str | None:
    if pattern.lower().startswith("exact:"):
        want = pattern.split(":", 1)[1].strip().lower()
        for name in mod_names:
            if name.lower() == want:
                return name
        return None
    needle = pattern.lower()
    for name in mod_names:
        if needle in name.lower():
            return name
    return None


_AXR_ASSIGN = re.compile(r"^(\s*)([^\s=]+)\s*=\s*(.*?)\s*$")


def format_axr_line(indent: str, key: str, value: str, width: int = 40) -> str:
    pad = max(width, len(key) + 1)
    return f"{indent}{key:<{pad}} = {value}"


def apply_settings_to_axr_options(
    path: Path,
    settings: list[InitSetting],
    dry_run: bool,
) -> list[str]:
    if not settings:
        return []
    lines = read_text_lines(path)
    changes: list[str] = []
    by_section: dict[str, list[InitSetting]] = {}
    for s in settings:
        by_section.setdefault(s.axr_section, []).append(s)

    for axr_section, sect_settings in by_section.items():
        header = f"[{axr_section}]"
        start = None
        for i, line in enumerate(lines):
            if line.strip().lower() == header.lower():
                start = i
                break
        if start is None:
            if dry_run:
                for s in sect_settings:
                    changes.append(f"[{axr_section}] {s.key} = {s.value} (new section)")
                continue
            if lines and lines[-1].strip() != "":
                lines.append("")
            lines.append(header)
            start = len(lines) - 1
            for s in sect_settings:
                lines.append(format_axr_line("        ", s.key, s.value))
                changes.append(f"[{axr_section}] {s.key} = {s.value} (added)")
            continue

        end = len(lines)
        for j in range(start + 1, len(lines)):
            if lines[j].strip().startswith("[") and lines[j].strip().endswith("]"):
                end = j
                break

        key_at: dict[str, int] = {}
        indent = "        "
        for i in range(start + 1, end):
            m = _AXR_ASSIGN.match(lines[i])
            if not m:
                continue
            indent = m.group(1) or indent
            key_at[m.group(2).lower()] = i

        for s in sect_settings:
            idx = key_at.get(s.key.lower())
            if idx is None:
                changes.append(f"[{axr_section}] {s.key} = {s.value} (added)")
                if not dry_run:
                    lines.insert(end, format_axr_line(indent, s.key, s.value))
                    end += 1
                continue
            m = _AXR_ASSIGN.match(lines[idx])
            assert m is not None
            old = m.group(3)
            if old == s.value:
                continue
            changes.append(f"[{axr_section}] {s.key}: {old} -> {s.value}")
            if not dry_run:
                lines[idx] = format_axr_line(m.group(1), m.group(2), s.value)

    if changes and not dry_run:
        stamp_backup(path)
        write_text_lines(path, lines)
    return changes


def iter_files(root: Path, name: str | None = None, suffix: str | None = None) -> Iterator[Path]:
    if not root.is_dir():
        return
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if name and path.name != name:
            continue
        if suffix and path.suffix.lower() != suffix.lower():
            continue
        yield path


def apply_initialize(
    mo2_root: Path,
    initialize_path: Path,
    modlist: Path,
    dry_run: bool,
    *,
    only_patterns: set[str] | None = None,
    installed: set[str] | None = None,
) -> tuple[int, int, list[str]]:
    if initialize_path.suffix.lower() in (".yml", ".yaml") or initialize_path.name == "manifest.yml":
        data = load_manifest(initialize_path)
        by_mod = data.collect_defaults(installed=installed)
    else:
        by_mod = read_initialize_ini(initialize_path)
    if not by_mod:
        return 0, 0, []
    mod_names = list_modlist_names(modlist)
    to_apply: list[InitSetting] = []
    skipped: list[str] = []
    for pattern, settings in by_mod.items():
        if only_patterns is not None and pattern.lower() not in only_patterns:
            continue
        if not settings:
            continue
        hit = find_present_mod(pattern, mod_names)
        if not hit:
            skipped.append(pattern)
            continue
        to_apply.extend(settings)
    if not to_apply:
        return 0, 0, skipped

    files = 0
    values = 0
    for scan in (mo2_root / "mods", mo2_root / "overwrite"):
        for path in iter_files(scan, name="axr_options.ltx"):
            # Only touch axr under a matched mod folder when possible
            changes = apply_settings_to_axr_options(path, to_apply, dry_run)
            if changes:
                files += 1
                values += len(changes)
    return files, values, skipped


def defaults_fingerprint(initialize_path: Path) -> dict[str, str]:
    """Hash each defaults section body for Update filtering."""
    by_mod = read_initialize_ini(initialize_path)
    out: dict[str, str] = {}
    for pattern, settings in by_mod.items():
        blob = "\n".join(f"{s.axr_section}/{s.key}={s.value}" for s in settings)
        out[pattern.lower()] = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return out


def load_fingerprint(mo2_dir: Path) -> dict[str, str]:
    path = mo2_dir / FINGERPRINT_NAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k).lower(): str(v) for k, v in data.items()}
    except (OSError, json.JSONDecodeError):
        return {}


def save_fingerprint(mo2_dir: Path, fp: dict[str, str]) -> None:
    path = mo2_dir / FINGERPRINT_NAME
    path.write_text(json.dumps(fp, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_dependencies(path: Path) -> list[Dependency]:
    """Load external mods from manifest.yml (accepts legacy dependencies.yml)."""
    if path.name in ("dependencies.yml", "dependencies.yaml"):
        # Legacy file shape: {dependencies: [...]} — load_manifest also accepts that key
        return load_manifest(path).mods
    return load_manifest(path).mods


# ---------------------------------------------------------------------------
# MO2 dependency install helpers
# ---------------------------------------------------------------------------


def filter_deps(
    deps: list[Dependency] | ManifestData,
    tier: str,
    *,
    min_level: str | int = "dev",
    installed: set[str] | None = None,
) -> list[Dependency]:
    """tier: required (feature requirements) | suggested | all."""
    if isinstance(deps, ManifestData):
        if tier == "required":
            return deps.feature_requirements(min_level, installed=installed)
        if tier == "suggested":
            return list(deps.suggested)
        if tier == "all":
            return deps.feature_requirements(min_level, installed=installed) + list(
                deps.suggested
            )
        raise ValueError(f"unknown tier: {tier}")
    t = tier.lower()
    if t == "all":
        req = [d for d in deps if d.tier == "required"]
        sug = [d for d in deps if d.tier == "suggested"]
        return req + sug
    if t == "required":
        return [d for d in deps if d.tier == "required"]
    if t == "suggested":
        return [d for d in deps if d.tier == "suggested"]
    raise ValueError(f"unknown tier: {tier}")


def grok_mods_txt(mo2_root: Path) -> Path:
    return mo2_root / ".Grok's Modpack Installer" / "mods.txt"


def catalog_rows_for_url(mo2_root: Path, url: str) -> list[tuple[int, str]]:
    """Return (1-based lineno, folder_name_guess) for mods.txt rows matching url."""
    path = grok_mods_txt(mo2_root)
    if not path.is_file() or not url:
        return []
    want = url.strip().lower()
    hits: list[tuple[int, str]] = []
    for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.strip() or line.startswith(" "):
            continue
        parts = line.split("\t")
        if not parts:
            continue
        if parts[0].strip().lower() != want:
            continue
        # displayName is usually field index 3 (0=url,1=fomod,2=author marker,3=name)
        display = parts[3].strip() if len(parts) > 3 else ""
        author = ""
        if len(parts) > 2:
            author = parts[2].replace(" - ", "").strip(" -")
        # GAMMA folder: "{lineno}- {displayName} - {Author}"
        if display and author:
            folder = f"{i}- {display} - {author}"
        elif display:
            folder = f"{i}- {display}"
        else:
            folder = f"{i}-"
        hits.append((i, folder))
    return hits


def find_catalog_folders(mo2_root: Path, url: str) -> list[str]:
    """Existing mods/ folders that match catalog lineno for url."""
    mods = mo2_root / "mods"
    found: list[str] = []
    for lineno, _guess in catalog_rows_for_url(mo2_root, url):
        prefix = f"{lineno}-"
        for d in mods.iterdir() if mods.is_dir() else []:
            if d.is_dir() and d.name.startswith(prefix):
                found.append(d.name)
    return found


def read_meta_url(mod_dir: Path) -> str:
    meta = mod_dir / "meta.ini"
    if not meta.is_file():
        return ""
    for line in meta.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip().lower().startswith("url="):
            return line.split("=", 1)[1].strip()
    return ""


def write_meta_url(mod_dir: Path, url: str) -> None:
    meta = mod_dir / "meta.ini"
    lines: list[str] = []
    if meta.is_file():
        lines = read_text_lines(meta)
    found_url = False
    found_custom = False
    out: list[str] = []
    for line in lines:
        low = line.strip().lower()
        if low.startswith("url="):
            out.append(f"url={url}")
            found_url = True
        elif low.startswith("hascustomurl="):
            out.append("hasCustomURL=true")
            found_custom = True
        else:
            out.append(line)
    if not found_url or not found_custom:
        # Ensure [General] block
        if not any(l.strip().lower() == "[general]" for l in out):
            out.insert(0, "[General]")
        if not found_url:
            out.append(f"url={url}")
        if not found_custom:
            out.append("hasCustomURL=true")
    write_text_lines(meta, out)


def managed_stamp_for(dep: Dependency) -> str:
    if dep.source == "user":
        return f"{USER_URL_PREFIX}{dep.id}"
    return dep.url


def find_managed_folders(mo2_root: Path, dep: Dependency) -> list[str]:
    stamp = managed_stamp_for(dep).lower()
    mods = mo2_root / "mods"
    found: list[str] = []
    if not mods.is_dir() or not stamp:
        return found
    for d in mods.iterdir():
        if not d.is_dir():
            continue
        if d.name.lower() == f"{MANAGED_FOLDER_PREFIX}{dep.id}".lower():
            found.append(d.name)
            continue
        url = read_meta_url(d).lower()
        if url and url == stamp:
            found.append(d.name)
    return found


def dep_is_satisfied(
    mo2_root: Path,
    dep: Dependency,
    modlist: Path | None = None,
    *,
    require_enabled: bool = False,
) -> tuple[bool, list[str]]:
    folders: list[str] = []
    if dep.url:
        folders.extend(find_catalog_folders(mo2_root, dep.url))
    for name in find_managed_folders(mo2_root, dep):
        if name not in folders:
            folders.append(name)
    if not folders:
        return False, []
    if not require_enabled or modlist is None:
        return True, folders
    enabled = {n for f, n in list_modlist_entries(modlist) if f == "+"}
    on = [f for f in folders if f in enabled]
    return (len(on) > 0, folders)


def ensure_separator(modlist: Path, dry_run: bool) -> None:
    lines = read_text_lines(modlist)
    for line in lines:
        m = re.match(r"^[+\-](.+)$", line)
        if m and m.group(1) == SEPARATOR_NAME:
            return
        if line.strip() == SEPARATOR_NAME:
            return
    # Insert near top after first few lines / after DOGMA separator if present
    insert_at = 0
    for i, line in enumerate(lines):
        if "DOGMA" in line.upper() and "separator" in line.lower():
            insert_at = i + 1
            break
    entry = f"+{SEPARATOR_NAME}"
    lines.insert(insert_at, entry)
    if not dry_run:
        stamp_backup(modlist)
        write_text_lines(modlist, lines)
    # Ensure empty folder exists so MO2 treats it as separator
    sep_dir = modlist.parents[2] / "mods" / SEPARATOR_NAME
    if not dry_run:
        sep_dir.mkdir(parents=True, exist_ok=True)
        meta = sep_dir / "meta.ini"
        if not meta.is_file():
            write_text_lines(meta, ["[General]", "modid=0", f"version=", "newestVersion=", "category=0"])


def insert_mod_under_separator(modlist: Path, mod_name: str, dry_run: bool) -> None:
    ensure_separator(modlist, dry_run)
    lines = read_text_lines(modlist)
    # Remove existing entry for this mod
    lines = [l for l in lines if not re.match(rf"^[+\-]{re.escape(mod_name)}$", l)]
    out: list[str] = []
    inserted = False
    for line in lines:
        out.append(line)
        m = re.match(r"^[+\-](.+)$", line)
        if m and m.group(1) == SEPARATOR_NAME and not inserted:
            out.append(f"+{mod_name}")
            inserted = True
    if not inserted:
        out.insert(0, f"+{SEPARATOR_NAME}")
        out.insert(1, f"+{mod_name}")
    if not dry_run:
        stamp_backup(modlist)
        write_text_lines(modlist, out)


def wipe_managed_mod(mo2_root: Path, modlist: Path, folder_name: str, dry_run: bool) -> None:
    # Never wipe Grok-numbered catalog folders
    if re.match(r"^\d+-", folder_name):
        warn(f"  Refusing to wipe catalog folder: {folder_name}")
        return
    lines = [l for l in read_text_lines(modlist) if not re.match(rf"^[+\-]{re.escape(folder_name)}$", l)]
    if not dry_run:
        stamp_backup(modlist)
        write_text_lines(modlist, lines)
        target = mo2_root / "mods" / folder_name
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)


def downloads_dir(mo2_root: Path) -> Path:
    d = mo2_root / "downloads" / "DOGMA"
    d.mkdir(parents=True, exist_ok=True)
    return d


def find_archive_for_dep(mo2_root: Path, dep: Dependency) -> Path | None:
    dld = downloads_dir(mo2_root)
    if dep.file:
        p = dld / dep.file
        return p if p.is_file() else None
    # Any archive whose name contains dep id
    needle = dep.id.lower()
    for p in sorted(dld.iterdir()) if dld.is_dir() else []:
        if p.is_file() and needle in p.stem.lower() and p.suffix.lower() in {
            ".zip", ".7z", ".rar", ".7zip",
        }:
            return p
    return None


def mo2_download(mo2_root: Path, url: str) -> int:
    exe = mo2_root / "ModOrganizer.exe"
    if not exe.is_file():
        raise FileNotFoundError(f"ModOrganizer.exe not found: {exe}")
    # Download into downloads/; MO2 may not put it under DOGMA/ — we still look both places
    info(f"  MO2 download: {url}")
    proc = subprocess.run([str(exe), "download", url], cwd=str(mo2_root), check=False)
    return proc.returncode


def extract_archive(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    suffix = archive.suffix.lower()
    if suffix == ".zip":
        with zipfile.ZipFile(archive, "r") as zf:
            zf.extractall(dest)
        return
    seven = find_7z()
    if not seven:
        raise RuntimeError(f"Need 7-Zip to extract {archive.name}. Install 7-Zip and re-run.")
    proc = subprocess.run(
        [str(seven), "x", f"-o{dest}", "-y", str(archive)],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"7z extract failed for {archive.name}: {proc.stderr or proc.stdout}")


def normalize_extracted_mod(dest: Path) -> None:
    """If archive had a single top-level folder, hoist its contents."""
    kids = [p for p in dest.iterdir() if p.name not in ("meta.ini",)]
    if len(kids) == 1 and kids[0].is_dir():
        inner = kids[0]
        for item in inner.iterdir():
            target = dest / item.name
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            shutil.move(str(item), str(target))
        inner.rmdir()


def run_after_unpack(mo2_root: Path, dep: Dependency, mod_dir: Path) -> None:
    if not dep.after_unpack:
        return
    script = Path(dep.after_unpack)
    if not script.is_file():
        script = script_dir() / dep.after_unpack
    if not script.is_file():
        warn(f"  after_unpack not found: {dep.after_unpack}")
        return
    info(f"  after_unpack: {script}")
    subprocess.run(
        [sys.executable, str(script), "--mo2-root", str(mo2_root), "--mod-dir", str(mod_dir)],
        check=False,
    )


def process_dependency(
    mo2_root: Path,
    modlist: Path,
    dep: Dependency,
    *,
    mode: str,
    dry_run: bool,
) -> str:
    """mode: reinstall | ensure. Returns status string."""
    ok_present, folders = dep_is_satisfied(mo2_root, dep)
    catalog = find_catalog_folders(mo2_root, dep.url) if dep.url else []

    if catalog:
        # Never wipe catalog — enable only
        enabled = enable_mods_in_modlist(modlist, catalog, dry_run)
        msg = f"catalog enable {catalog}" + (f" (newly: {enabled})" if enabled else " (already on)")
        info(f"  [{dep.id}] {msg}")
        return "catalog"

    managed = find_managed_folders(mo2_root, dep)
    if mode == "reinstall":
        for name in managed:
            info(f"  [{dep.id}] wipe managed: {name}")
            wipe_managed_mod(mo2_root, modlist, name, dry_run)
        managed = []
        ok_present = False

    if managed:
        enable_mods_in_modlist(modlist, managed, dry_run)
        info(f"  [{dep.id}] already present: {managed}")
        return "present"

    if ok_present and folders:
        enable_mods_in_modlist(modlist, folders, dry_run)
        info(f"  [{dep.id}] already present: {folders}")
        return "present"

    # Need install
    archive = find_archive_for_dep(mo2_root, dep)
    if dep.source == "user":
        if not archive:
            howto = dep.howto or f"Place the zip as downloads/DOGMA/{dep.file or (dep.id + '.zip')}"
            raise FileNotFoundError(f"[{dep.id}] user-sourced archive missing. {howto}")
    else:
        if not archive and dep.url and not dry_run:
            rc = mo2_download(mo2_root, dep.url)
            if rc != 0:
                warn(f"  [{dep.id}] MO2 download exit {rc}; checking downloads…")
            archive = find_archive_for_dep(mo2_root, dep)
            if not archive:
                # Also scan top-level downloads/
                top = mo2_root / "downloads"
                for p in top.glob("*") if top.is_dir() else []:
                    if p.is_file() and dep.id.lower() in p.stem.lower():
                        dest = downloads_dir(mo2_root) / p.name
                        if not dry_run:
                            shutil.copy2(p, dest)
                        archive = dest
                        break
        if not archive and not dry_run:
            raise FileNotFoundError(
                f"[{dep.id}] archive not found after download. URL={dep.url}"
            )

    folder_name = f"{MANAGED_FOLDER_PREFIX}{dep.id}"
    dest = mo2_root / "mods" / folder_name
    info(f"  [{dep.id}] install → {folder_name} from {archive}")
    if dry_run:
        return "would-install"
    if dest.exists():
        shutil.rmtree(dest)
    assert archive is not None
    extract_archive(archive, dest)
    normalize_extracted_mod(dest)
    write_meta_url(dest, managed_stamp_for(dep))
    insert_mod_under_separator(modlist, folder_name, dry_run=False)
    run_after_unpack(mo2_root, dep, dest)
    return "installed"


def gather_dep_disable_rules(
    mo2_root: Path,
    deps: list[Dependency],
    modlist: Path,
) -> list[Rule]:
    rules: list[Rule] = []
    for dep in deps:
        if not dep.disable:
            continue
        satisfied, _ = dep_is_satisfied(
            mo2_root, dep, modlist, require_enabled=True
        )
        if not satisfied:
            continue
        rules.extend(rules_from_disable_names(dep.disable, source=f"dep:{dep.id}"))
    return rules


def gather_dep_enable_rules(
    mo2_root: Path,
    deps: list[Dependency],
    modlist: Path,
) -> list[Rule]:
    """Enable rules from satisfied deps (requirements / suggested)."""
    rules: list[Rule] = []
    for dep in deps:
        if not dep.enable:
            continue
        satisfied, _ = dep_is_satisfied(
            mo2_root, dep, modlist, require_enabled=True
        )
        if not satisfied:
            continue
        rules.extend(rules_from_disable_names(dep.enable, source=f"dep:{dep.id}"))
    return rules


# ---------------------------------------------------------------------------
# validate / report
# ---------------------------------------------------------------------------

def build_report(
    mo2_root: Path,
    cfg: Path,
    *,
    tier: str = "required",
    profile: str = "",
) -> Path:
    tools = mo2_tools_dir(mo2_root)
    tools.mkdir(parents=True, exist_ok=True)
    report_path = tools / REPORT_LOG_NAME
    lines: list[str] = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"DOGMA MO2 report — {now}")
    lines.append(f"MO2 root: {mo2_root}")
    lines.append(f"Config  : {cfg}")
    lines.append(f"Tier    : {tier}")
    lines.append("")

    warns = 0
    oks = 0

    def W(msg: str) -> None:
        nonlocal warns
        warns += 1
        lines.append(f"WARN: {msg}")

    def O(msg: str) -> None:
        nonlocal oks
        oks += 1
        lines.append(f"OK: {msg}")

    if not pyyaml_ok():
        W("PyYAML not importable — run DOGMA (Setup Tools).bat")
    else:
        O("PyYAML importable")

    if find_7z():
        O(f"7-Zip found: {find_7z()}")
    else:
        W("7-Zip not found (needed for .7z/.rar archives)")

    modlist = modlist_path(mo2_root, profile)
    enabled = {n for f, n in list_modlist_entries(modlist) if f == "+"}
    lines.append(f"Profile : {selected_profile(mo2_root, profile)}")
    lines.append("")

    # Unified manifest.yml
    try:
        man_path = resolve_manifest_path(cfg)
        data = load_manifest(man_path)
        installed = resolve_installed_features(mo2_root, data)
        lines.append(f"Manifest: {man_path}")
        if installed is not None:
            feat_n = len([f for f in installed if f.lower() != "common"])
            lines.append(f"Installed DOGMA features detected: {feat_n}")
        rules, active, _skipped = feature_disable_rules(data, installed=installed)
        lines.append(f"Active features with disable rules: {', '.join(active) or '(none)'}")
        for name in sorted(enabled):
            rule = mod_matches(name, rules)
            if rule:
                feat = rule.source or (rule.features[0] if rule.features else "?")
                W(f'"{name}" is enabled but feature "{feat}" lists it as a conflict')
        if not any(mod_matches(n, rules) for n in enabled):
            O("No enabled mods conflict with active feature disables")

        enable_rules, enable_active = feature_enable_rules(data, installed=installed)
        if enable_active:
            lines.append(
                f"Active features with enable rules: {', '.join(enable_active)}"
            )
        disabled_names = {n for f, n in list_modlist_entries(modlist) if f == "-"}
        for name in sorted(disabled_names):
            rule = mod_matches(name, enable_rules)
            if rule:
                feat = rule.source or (rule.features[0] if rule.features else "?")
                W(f'"{name}" is disabled but feature "{feat}" lists it to enable')
        if enable_rules and not any(mod_matches(n, enable_rules) for n in disabled_names):
            O("No disabled mods missing from active feature enable lists")

        lines.append("")
        check = filter_deps(data, tier, installed=installed)
        for dep in check:
            sat, folders = dep_is_satisfied(
                mo2_root, dep, modlist, require_enabled=True
            )
            present, _ = dep_is_satisfied(mo2_root, dep, require_enabled=False)
            if not present:
                W(f'{dep.tier} mod "{dep.id}" is missing')
                if dep.source == "user":
                    arch = find_archive_for_dep(mo2_root, dep)
                    if not arch:
                        W(
                            f'mod "{dep.id}" expected zip missing: '
                            f'downloads/DOGMA/{dep.file or (dep.id + ".zip")} (user-sourced)'
                        )
            elif not sat:
                W(f'{dep.tier} mod "{dep.id}" is installed but disabled ({folders})')
            else:
                O(f'mod "{dep.id}" satisfied via {folders}')
                dep_rules = rules_from_disable_names(dep.disable, source=f"mod:{dep.id}")
                for name in sorted(enabled):
                    rule = mod_matches(name, dep_rules)
                    if rule:
                        W(
                            f'"{name}" is enabled but mod "{dep.id}" '
                            f"lists it as a conflict"
                        )
                dep_en = rules_from_disable_names(dep.enable, source=f"mod:{dep.id}")
                for name in sorted(disabled_names):
                    rule = mod_matches(name, dep_en)
                    if rule:
                        W(
                            f'"{name}" is disabled but mod "{dep.id}" '
                            f"lists it to enable"
                        )
    except Exception as exc:  # noqa: BLE001
        W(f"manifest.yml error: {exc}")

    lines.append("")
    lines.append(f"Summary: {warns} WARN, {oks} OK")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    append_action_log(tools, f"report written → {report_path} ({warns} WARN, {oks} OK)")
    return report_path


def mo2_refresh(mo2_root: Path) -> None:
    exe = mo2_root / "ModOrganizer.exe"
    if not exe.is_file():
        return
    info("Refreshing MO2…")
    subprocess.run([str(exe), "refresh"], cwd=str(mo2_root), check=False)


def guard_mo2_closed(*, force: bool, dry_run: bool) -> None:
    if dry_run:
        return
    if mo2_running() and not force:
        raise RuntimeError(
            "ModOrganizer.exe is running. Close MO2, then run again "
            "(or pass --force; edits may be lost on MO2 exit)."
        )
    if mo2_running() and force:
        warn("WARNING: ModOrganizer.exe is running (--force). Edits may be lost when MO2 exits.")

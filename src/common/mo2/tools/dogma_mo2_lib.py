#!/usr/bin/env python3
"""DOGMA MO2 shared library — deps, disable, defaults, validate report, paths."""

from __future__ import annotations

import configparser
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import zipfile
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Iterator

# Parallel wizard downloads: ModDB/GitHub resolve can overlap; claim +
# archives.ini must stay serialized. HTTP downloads themselves may run in parallel.
_downloads_claim_lock = threading.Lock()
_archive_map_lock = threading.Lock()
_legacy_migrate_lock = threading.Lock()
_legacy_migrated_roots: set[str] = set()


SEPARATOR_NAME = "DOGMA DEPENDENCIES_separator"
ACTION_LOG_NAME = "dogma_install.log"
SFX_LOG_NAME = "dogma_sfx_prefetch.log"
REPORT_LOG_NAME = "dogma_report.log"
MODDB_CACHE_NAME = "moddb_cache.json"
GITHUB_CACHE_NAME = "github_cache.json"
USER_URL_PREFIX = "dogma:user:"
MANAGED_FOLDER_PREFIX = "DOGMA - "
MODDB_CACHE_MAX_AGE_S = 6 * 3600
GITHUB_CACHE_MAX_AGE_S = 6 * 3600


# ---------------------------------------------------------------------------
# console / log
# ---------------------------------------------------------------------------

_COLOR = False  # set in _init_color()
_log_tools: Path | None = None
_log_name: str = ACTION_LOG_NAME


def _init_color() -> bool:
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


_COLOR = _init_color()


def action_log_name_for_job(job: str) -> str:
    if job == "sfx":
        return SFX_LOG_NAME
    return ACTION_LOG_NAME


def action_log_path(mo2_dir: Path) -> Path:
    return mo2_dir / "logs" / _log_name


def report_log_path(mo2_dir: Path) -> Path:
    return mo2_dir / "logs" / REPORT_LOG_NAME


def configure_logging(mo2_dir: Path, *, reset: bool = False, job: str = "") -> Path:
    """Tee console output into mods/DOGMA/mo2/logs/<action log>.

    Most jobs share dogma_install.log. The sfx job uses dogma_sfx_prefetch.log
    (always truncated per run) so prefetch noise never lands in the install log.
    """
    global _log_tools, _log_name
    mo2_dir.mkdir(parents=True, exist_ok=True)
    _log_tools = mo2_dir
    _log_name = action_log_name_for_job(job)
    path = action_log_path(mo2_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Dedicated sfx log: always start fresh so each run is readable.
    do_reset = reset or _log_name == SFX_LOG_NAME or not path.is_file()
    if do_reset:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if _log_name == SFX_LOG_NAME:
            header = f"=== DOGMA SFX prefetch log - {stamp} ===\n"
        else:
            header = f"=== DOGMA install log - {stamp} ===\n"
        if job:
            header += f"job: {job}\n"
        path.write_text(header + "\n", encoding="utf-8")
    elif job:
        append_action_log(mo2_dir, f"--- job: {job} ---")
    return path


def append_action_log(mo2_dir: Path | None, line: str) -> None:
    tools = mo2_dir if mo2_dir is not None else _log_tools
    if tools is None:
        return
    path = action_log_path(tools)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {line}\n")
            fh.flush()
    except OSError as exc:
        print(f"(log write failed: {exc}) {line}", file=sys.stderr)


def _tee(level: str, msg: str) -> None:
    if _log_tools is not None:
        append_action_log(_log_tools, f"{level}: {msg}" if level else msg)


def info(msg: str) -> None:
    print(msg)
    _tee("INFO", msg)


def ok(msg: str) -> None:
    print(f"\033[32m{msg}\033[0m" if _COLOR else msg)
    _tee("OK", msg)


def warn(msg: str) -> None:
    print(f"\033[33m{msg}\033[0m" if _COLOR else msg)
    _tee("WARN", msg)


def err(msg: str) -> None:
    print(f"\033[31m{msg}\033[0m" if _COLOR else msg, file=sys.stderr)
    _tee("ERROR", msg)


def log_exception(exc: BaseException, *, where: str = "") -> None:
    """Log an exception and full traceback (console + install log)."""
    import traceback

    prefix = f"{where}: " if where else ""
    err(f"{prefix}{type(exc).__name__}: {exc}")
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    for line in tb.rstrip().splitlines():
        _tee("TRACE", line)
        print(line, file=sys.stderr)


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------

def script_dir() -> Path:
    """Directory containing dogma_mo2_lib.py (…/mo2/tools/)."""
    return Path(__file__).resolve().parent


def mo2_bundle_dir() -> Path:
    """mods/DOGMA/mo2/ — sibling of ``tools/`` (packages, config, logs)."""
    d = script_dir()
    if d.name.lower() == "tools":
        return d.parent
    return d


def resolve_mo2_root(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit)
    # When registered as MO2 executable, cwd is instance root.
    cwd = Path.cwd()
    if (cwd / "ModOrganizer.exe").is_file():
        return cwd
    # Deployed under mods/DOGMA/mo2[/tools]/ mo2 → DOGMA → mods → MO2 root
    candidate = mo2_bundle_dir().parent.parent.parent
    if (candidate / "ModOrganizer.exe").is_file():
        return candidate
    default = Path(r"C:\GAMMA") if sys.platform == "win32" else Path(os.environ.get("MO2_ROOT", r"C:\GAMMA"))
    return default


def resolve_config_dir(mo2_root: Path, override: Path | None = None) -> Path:
    if override and override.is_dir():
        return override

    def _ok(cfg: Path) -> bool:
        return (
            (cfg / "features.yml").is_file()
            or (cfg / "manifest.yml").is_file()
            or (cfg / "manifest-third-party.yml").is_file()
            or (cfg / "manifest-remote.yml").is_file()
            or (cfg / "manifest-dogma-features.yml").is_file()
            or (cfg / "manifest-dogma-mods.yml").is_file()
            or (cfg / "manifest-dogma-tweaks.yml").is_file()
            or (cfg / "manifest-local.yml").is_file()
        )

    staged = mo2_bundle_dir() / "config"
    if staged.is_dir() and _ok(staged):
        return staged
    # Author / repo: src/common/mo2 → ../../../config
    repo_cfg = mo2_bundle_dir().parent.parent.parent / "config"
    if repo_cfg.is_dir() and _ok(repo_cfg):
        return repo_cfg
    dogma = mo2_root / "mods" / "DOGMA" / "mo2" / "config"
    if dogma.is_dir() and _ok(dogma):
        return dogma
    return staged


def split_manifest_paths(cfg_dir: Path) -> list[Path]:
    """Ordered split catalogs when all parts exist.

    Preferred: ``manifest-third-party`` + ``manifest-dogma-features`` + ``manifest-dogma-tweaks``.
    Legacy: ``manifest-dogma-mods`` / ``manifest-remote`` / ``manifest-local``.
    """
    preferred = (
        "manifest-third-party",
        "manifest-dogma-features",
        "manifest-dogma-tweaks",
    )
    legacy_mods_name = (
        "manifest-third-party",
        "manifest-dogma-mods",
        "manifest-dogma-tweaks",
    )
    legacy_remote = (
        "manifest-remote",
        "manifest-dogma-features",
        "manifest-dogma-tweaks",
    )
    legacy_remote_mods = (
        "manifest-remote",
        "manifest-dogma-mods",
        "manifest-dogma-tweaks",
    )
    legacy = ("manifest-remote", "manifest-local")

    def _collect(bases: tuple[str, ...]) -> list[Path]:
        out: list[Path] = []
        for base in bases:
            found: Path | None = None
            for ext in (".yml", ".yaml"):
                p = cfg_dir / f"{base}{ext}"
                if p.is_file():
                    found = p
                    break
            if found is None:
                return []
            out.append(found)
        return out

    for bases in (
        preferred,
        legacy_mods_name,
        legacy_remote,
        legacy_remote_mods,
        legacy,
    ):
        parts = _collect(bases)
        if parts:
            return parts
    return []


def resolve_manifest_path(cfg_dir: Path) -> Path:
    """Catalog entry: config dir (split manifests) or unified manifest/features file."""
    if split_manifest_paths(cfg_dir):
        return cfg_dir
    for name in ("manifest.yml", "manifest.yaml", "features.yml", "features.yaml"):
        yml = cfg_dir / name
        if yml.is_file():
            return yml
    raise FileNotFoundError(
        f"split manifests, manifest.yml, or features.yml not found under {cfg_dir}"
    )


def resolve_options_path(cfg_dir: Path) -> Path | None:
    for name in ("options.yml", "options.yaml", "installer_options.yml"):
        p = cfg_dir / name
        if p.is_file():
            return p
    return None


def resolve_mods_path(cfg_dir: Path) -> Path | None:
    for name in ("mods.yml", "mods.yaml", "suggested_mods.yml"):
        p = cfg_dir / name
        if p.is_file():
            return p
    return None


def resolve_suggestions_path(cfg_dir: Path) -> Path | None:
    """Legacy combined suggestions.yml (options + mods in one file)."""
    for name in ("suggestions.yml", "suggested.yml"):
        p = cfg_dir / name
        if p.is_file():
            return p
    return None


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


# Shown in MO2 Restore Backup… as the choice label (suffix after modlist.txt.).
PREINSTALL_BACKUP_PREFIX = "DOGMA Pre Install Backup"


def next_preinstall_backup_suffix(modlist: Path) -> str:
    """Next ``DOGMA Pre Install Backup N`` suffix for MO2 Restore Backup."""
    pat = re.compile(
        rf"^{re.escape(modlist.name)}\."
        rf"{re.escape(PREINSTALL_BACKUP_PREFIX)} (\d+)$",
        re.IGNORECASE,
    )
    n_max = 0
    for p in modlist.parent.iterdir():
        if not p.is_file():
            continue
        m = pat.match(p.name)
        if m:
            n_max = max(n_max, int(m.group(1)))
    return f"{PREINSTALL_BACKUP_PREFIX} {n_max + 1}"


def create_preinstall_modlist_backup(
    mo2_root: Path,
    profile: str = "",
    *,
    dry_run: bool = False,
) -> Path:
    """Copy profile modlist.txt like MO2's Create Backup (left-pane).

    MO2 Restore Backup lists ``modlist.txt.<suffix>``; we use an incrementing
    ``DOGMA Pre Install Backup N`` suffix so it is easy to pick.
    """
    modlist = modlist_path(mo2_root, profile)
    suffix = next_preinstall_backup_suffix(modlist)
    dest = modlist.parent / f"{modlist.name}.{suffix}"
    if dry_run:
        info(f"Would create MO2 modlist backup: {dest.name}")
        return dest
    shutil.copy2(modlist, dest)
    ok(f"MO2 modlist backup: {dest.name}")
    info("  Restore via MO2 → Restore Backup… on the mod list")
    return dest


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


# axr_options.ltx section for settings: entries
_SETTINGS_AXR_SECTION = "options"


def _parse_kv_entries(raw, *, field: str) -> dict[str, str]:
    """Parse a list of {path: value} maps, or a flat path: value mapping."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        if any(isinstance(v, (dict, list)) for v in raw.values()):
            raise ValueError(f"{field}: want path: value pairs (not nested groups)")
        return {str(k).strip(): str(v).strip() for k, v in raw.items() if str(k).strip()}
    if not isinstance(raw, list):
        raise ValueError(f"{field} must be a list of path: value entries (or a mapping)")
    out: dict[str, str] = {}
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or not item:
            raise ValueError(f"{field}[{i}] must be a path: value mapping")
        for key, val in item.items():
            key_s = str(key).strip()
            if not key_s:
                raise ValueError(f"{field}[{i}] empty path")
            if isinstance(val, (dict, list)):
                raise ValueError(f"{field}[{i}].{key_s}: value must be a scalar")
            out[key_s] = str(val).strip()
    return out


def _parse_moves(raw, *, field: str) -> list[tuple[str, str]]:
    """Parse moves: list of {src_rel: dest} → [(src, dest), ...]."""
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{field} must be a list of src: dest mappings")
    out: list[tuple[str, str]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or not item:
            raise ValueError(f"{field}[{i}] must be a src: dest mapping")
        for src, dest in item.items():
            src_s = str(src).strip().replace("\\", "/")
            dest_s = str(dest).strip()
            if not src_s or not dest_s:
                raise ValueError(f"{field}[{i}] empty src or dest")
            if isinstance(dest, (dict, list)):
                raise ValueError(f"{field}[{i}]: dest must be a scalar path")
            out.append((src_s, dest_s))
    return out


def _parse_str_list(raw, *, field: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{field} must be a list")
    out: list[str] = []
    for x in raw:
        s = str(x).strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def _parse_radio_option_groups(raw, *, field: str) -> list[list[str]]:
    """Parse radio ``options:`` — each entry is one choice (one pack or several).

    Supported YAML shapes::

        options:
          - Pack A
          - Pack B
          - [Pack A, Pack B]          # flow list → install both
          -                             # nested list → install both
            - Pack A
            - Pack B
          - |                           # one pack id per line
            Pack A
            Pack B
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{field} must be a list")
    groups: list[list[str]] = []
    for i, item in enumerate(raw):
        if isinstance(item, list):
            ids = [str(x).strip() for x in item if str(x).strip()]
            if not ids:
                raise ValueError(f"{field}[{i}]: empty choice list")
            groups.append(ids)
            continue
        if isinstance(item, str):
            lines = [
                ln.strip()
                for ln in item.replace("\r\n", "\n").split("\n")
                if ln.strip() and not ln.strip().startswith("#")
            ]
            if not lines:
                raise ValueError(f"{field}[{i}]: empty choice")
            groups.append(lines)
            continue
        raise ValueError(
            f"{field}[{i}]: expected a pack id string or a list of pack ids, "
            f"got {type(item).__name__}"
        )
    return groups


def _inline_composition_id(pack_ids: list[str]) -> str:
    """Build a stable id for an inline multi-pack radio choice."""
    ids = [str(x).strip() for x in pack_ids if str(x).strip()]
    if not ids:
        return ""
    if len(ids) == 1:
        return ids[0]
    parts = [i.split() for i in ids]
    common: list[str] = []
    for words in zip(*parts):
        if len(set(w.lower() for w in words)) == 1:
            common.append(words[0])
        else:
            break
    if common:
        tails = [" ".join(p[len(common) :]) for p in parts]
        if all(tails):
            return f"{' '.join(common)} {' + '.join(tails)}"
    return " + ".join(ids)


def _materialize_inline_radio_compositions(
    suggested: list[Dependency],
) -> list[Dependency]:
    """Turn multi-pack ``options:`` entries into omit composition packs (requires:).

    ``options: [A, B, [A, B]]`` becomes choice ids ``A``, ``B``, and a synthetic
    composition pack (e.g. ``A + B``) when one is not already defined.
    """
    by_id = {d.id: d for d in suggested}
    extras: list[Dependency] = []
    for dep in suggested:
        groups = list(dep.option_groups)
        if not groups:
            # Legacy / already flat options: treat each as a single-pack choice.
            if dep.options and not dep.option_groups:
                dep.option_groups = [[oid] for oid in dep.options]
                groups = list(dep.option_groups)
            else:
                continue
        new_options: list[str] = []
        for group in groups:
            if len(group) == 1:
                new_options.append(group[0])
                continue
            syn_id = _inline_composition_id(group)
            if syn_id not in by_id:
                syn = Dependency(
                    id=syn_id,
                    tier="suggested",
                    stage="omit",
                    requires=list(group),
                )
                extras.append(syn)
                by_id[syn_id] = syn
            new_options.append(syn_id)
        dep.options = new_options
        dep.option_groups = [list(g) for g in groups]
    return suggested + extras


def _reject_legacy_defaults(item: dict, *, field: str) -> None:
    if item.get("defaults") is not None:
        raise ValueError(
            f"{field}: defaults: removed — use top-level resets: / mcm: / settings:"
        )
    if item.get("target_mod") is not None:
        raise ValueError(
            f"{field}: target_mod removed — put keys under mcm: / settings:"
        )


def _pack_effect_fields(item: dict, *, section: str) -> dict:
    """Parse shared pack effect fields from a YAML map."""
    _reject_legacy_defaults(item, field=section)
    return {
        "resets": _parse_str_list(item.get("resets"), field=f"{section}.resets"),
        "mcm": _parse_kv_entries(item.get("mcm"), field=f"{section}.mcm"),
        "settings": _parse_kv_entries(item.get("settings"), field=f"{section}.settings"),
        "moves": _parse_moves(item.get("moves"), field=f"{section}.moves"),
        "deletes": _parse_str_list(item.get("deletes"), field=f"{section}.deletes"),
        "console": _parse_str_list(item.get("console"), field=f"{section}.console"),
    }


def _overrides_to_settings(
    source: str,
    *,
    mcm: dict[str, str],
    settings: dict[str, str],
) -> list[InitSetting]:
    out: list[InitSetting] = []
    for key, val in mcm.items():
        out.append(InitSetting(source, "mcm", key, val))
    for key, val in settings.items():
        out.append(InitSetting(source, _SETTINGS_AXR_SECTION, key, val))
    return out


STAGE_RANK = {"omit": 0, "dev": 1, "release": 2}
STAGE_ALIASES = {
    "0": "omit",
    "1": "dev",
    "2": "release",
    "omit": "omit",
    "dev": "dev",
    "release": "release",
    # legacy
    "local": "dev",
    "off": "omit",
    "hidden": "omit",
}


def parse_stage(raw) -> str:
    if raw is False:
        return "omit"
    if raw is True:
        raise ValueError(
            "stage must be omit|dev|release (got boolean true — quote strings in YAML)"
        )
    key = str(raw).strip().lower()
    if key not in STAGE_ALIASES:
        raise ValueError(f"stage must be omit|dev|release (got {raw!r})")
    return STAGE_ALIASES[key]


def stage_meets(stage: str, minimum: str) -> bool:
    return STAGE_RANK[parse_stage(stage)] >= STAGE_RANK[parse_stage(minimum)]


# Compat aliases
LEVEL_RANK = STAGE_RANK
LEVEL_ALIASES = STAGE_ALIASES
parse_level = parse_stage
level_meets = stage_meets


@dataclass
class Dependency:
    id: str
    tier: str  # downloads (from a feature) | suggested
    # Canonical auto-download target (filled from url_download:).
    url: str = ""
    # Compat: first of url_kofi / url_patreon (legacy buy_url: maps here).
    buy_url: str = ""
    # Typed wizard links — each field is independent (own icon).
    url_moddb: str = ""
    url_github: str = ""
    url_discord: str = ""
    url_download: str = ""  # MO2 auto-fetch (ModDB or GitHub file/release)
    url_kofi: str = ""
    url_patreon: str = ""
    path: str = ""  # local src/<path> feature (package zip); mutually exclusive w/ urls
    # omit|dev|release — FOMOD + wizard gate (empty = leaf / not a wizard entry)
    stage: str = ""
    archive_name: str = ""
    source: str = "auto"  # auto | user
    howto: str = ""
    desc: str = ""  # wizard blurb for option choices
    disables: list[str] = field(default_factory=list)
    enables: list[str] = field(default_factory=list)
    resets: list[str] = field(default_factory=list)  # MCM roots → script def=
    mcm: dict[str, str] = field(default_factory=dict)  # → [mcm]
    settings: dict[str, str] = field(default_factory=dict)  # → [options]
    moves: list[tuple[str, str]] = field(default_factory=list)  # (src_rel, dest)
    deletes: list[str] = field(default_factory=list)
    console: list[str] = field(default_factory=list)  # first-launch console cmds
    requires: list[str] = field(default_factory=list)  # install these packs first
    # Wizard radio group (legacy): packs sharing exclusive: appear as one section
    exclusive: str = ""
    group: str = ""  # section title override
    choice: str = ""  # radio label override
    # Compositional options (preferred):
    #   stage: release + options: [A, B] → wizard A|B (exclusive)
    #   options entry may be a pack id, or a list of pack ids (inline A+B choice)
    options: list[str] = field(default_factory=list)
    # Raw options: before materialize — each choice is one or more pack ids.
    option_groups: list[list[str]] = field(default_factory=list)
    after_unpack: str = ""
    feature: str = ""  # owning feature path when from features.*.requires

    # Wizard checkbox metadata (stage != omit on the same block).
    # Default checked state is derived (not buy_url) — see option_default_selected.
    wizard_requires: list[str] = field(default_factory=list)  # radio parents from requires:

    @property
    def wizard(self) -> bool:
        """True when this entry appears in the Setup wizard (stage != omit)."""
        if not self.stage:
            return False
        return parse_stage(self.stage) != "omit"

    @property
    def fomod(self) -> str:
        """Compat: path-mod FOMOD gate is the same as stage."""
        return self.stage or "omit"

    def has_axr_effects(self) -> bool:
        return bool(self.resets or self.mcm or self.settings)

    def has_remote_links(self) -> bool:
        return bool(
            self.url
            or self.buy_url
            or self.url_moddb
            or self.url_github
            or self.url_discord
            or self.url_download
            or self.url_kofi
            or self.url_patreon
        )

    def has_install_work(self) -> bool:
        """True if this pack is a download/install/enable unit (not a pure group)."""
        return bool(self.has_remote_links() or self.path or self.source == "user") or bool(
            self.enables or self.disables or self.moves or self.deletes
            or self.console or self.mcm or self.settings or self.resets
        )


@dataclass
class InstallerOption:
    """Wizard checkbox derived from embedded metadata in config/mods.yml."""

    id: str
    desc: str = ""
    mods: list[str] = field(default_factory=list)
    default: bool = False
    # mods.yml pack ids that expose options: (or legacy exclusive:);
    # selecting this checkbox requires a non-None pick for each.
    requires: list[str] = field(default_factory=list)


@dataclass
class InstallerSelection:
    """Saved wizard result: checkbox options + pack option picks."""

    option_ids: list[str] = field(default_factory=list)
    # parent pack id → chosen child option id ("" = None)
    exclusive_picks: dict[str, str] = field(default_factory=dict)
    # True when this selection was saved with the features page available
    # (even if zero features checked). Distinguishes "user cleared features"
    # from legacy selections that predate page 2.
    features_chosen: bool = False


DOGMA_NAME_PREFIX = "D.O.G.M.A."


def with_dogma_prefix(name: str) -> str:
    """Prefix a D.O.G.M.A. path-mod display name if not already prefixed."""
    n = (name or "").strip()
    if not n:
        return n
    if n.upper().startswith("D.O.G.M.A."):
        return n
    return f"{DOGMA_NAME_PREFIX} {n}"


@dataclass
class FeatureMeta:
    path: str
    stage: str  # omit | dev | release
    title: str = ""  # display name from features.yml key
    disables: list[str] = field(default_factory=list)
    enables: list[str] = field(default_factory=list)
    resets: list[str] = field(default_factory=list)
    mcm: dict[str, str] = field(default_factory=dict)
    settings: dict[str, str] = field(default_factory=dict)
    moves: list[tuple[str, str]] = field(default_factory=list)
    deletes: list[str] = field(default_factory=list)
    console: list[str] = field(default_factory=list)
    # Pack ids and/or other feature paths/titles — resolved via catalog.
    requires: list[str] = field(default_factory=list)
    # Legacy inline downloads (always empty after parse; rejected if non-empty).
    downloads: list[Dependency] = field(default_factory=list)

    @property
    def always_on(self) -> bool:
        return self.path.lower() == "common"

    @property
    def display_name(self) -> str:
        return with_dogma_prefix((self.title or self.path).strip() or self.path)

    @property
    def level(self) -> str:
        """Compat alias for stage."""
        return self.stage

    def has_axr_effects(self) -> bool:
        return bool(self.resets or self.mcm or self.settings)

def dogma_mod_dir(mo2_root: Path) -> Path:
    return mo2_root / "mods" / "DOGMA"


def feature_packages_dir(mo2_root: Path) -> Path:
    """Bundled feature zips: mods/DOGMA/mo2/packages/<path_key>.zip."""
    return mo2_tools_dir(mo2_root) / "packages"


def resolve_feature_packages_dir(
    *,
    mo2_root: Path | None = None,
    catalog_path: Path | None = None,
) -> Path | None:
    """Locate packages/ next to a live MO2 install or mo2/config catalog."""
    if mo2_root is not None:
        return feature_packages_dir(Path(mo2_root))
    if catalog_path is not None:
        p = Path(catalog_path)
        cfg_dir = p if p.is_dir() else p.parent
        if cfg_dir.name.lower() == "config" and cfg_dir.parent.name.lower() == "mo2":
            return cfg_dir.parent / "packages"
    return None


def feature_package_zip(
    feature_path: str,
    *,
    mo2_root: Path | None = None,
    packages_dir: Path | None = None,
) -> Path | None:
    """Return path to the local feature zip if packages_dir/mo2_root is known."""
    pkg = packages_dir
    if pkg is None and mo2_root is not None:
        pkg = feature_packages_dir(mo2_root)
    if pkg is None:
        return None
    return pkg / f"{feature_path_key(feature_path)}.zip"


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
    min_stage: str = "local",
    *,
    installed: set[str] | None = None,
) -> bool:
    """Active = manifest stage OK, and (if given) feature is installed in MO2."""
    if meta.always_on:
        return True
    if not stage_meets(meta.stage, min_stage):
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
    installer_options: list[InstallerOption] = field(default_factory=list)
    defaults: list[InitSetting] = field(default_factory=list)

    def suggested_by_id(self) -> dict[str, Dependency]:
        return {d.id: d for d in self.suggested}

    def resolve_feature_path(self, ref: str) -> str | None:
        """Map a features.yml path or display title → canonical path."""
        rid = str(ref or "").strip().replace("\\", "/")
        if not rid:
            return None
        if rid in self.features:
            return rid
        low = rid.lower()
        for path, meta in self.features.items():
            if path.lower() == low:
                return path
            if meta.title and meta.title.lower() == low:
                return path
            if meta.display_name.lower() == low:
                return path
        return None

    def feature_pack_ids(
        self,
        feature_path: str,
        *,
        _stack: set[str] | None = None,
    ) -> list[str]:
        """Pack ids required by a feature (requires: packs + nested features)."""
        stack = _stack if _stack is not None else set()
        fp = self.resolve_feature_path(feature_path)
        if not fp:
            raise ValueError(f"unknown feature in requires: {feature_path!r}")
        if fp in stack:
            raise ValueError(f"features requires: cycle involving {fp!r}")
        meta = self.features[fp]
        pack_by_id = self.suggested_by_id()
        out: list[str] = []
        seen: set[str] = set()
        stack.add(fp)
        try:
            for ref in meta.requires:
                rid = str(ref).strip()
                if not rid:
                    continue
                nested = self.resolve_feature_path(rid)
                if nested is not None:
                    for pid in self.feature_pack_ids(nested, _stack=stack):
                        if pid not in seen:
                            seen.add(pid)
                            out.append(pid)
                    continue
                if rid not in pack_by_id:
                    raise ValueError(
                        f"features.{meta.display_name!r} ({fp}): requires entry "
                        f"{rid!r} is neither a mods.yml pack nor a feature "
                        f"path/title"
                    )
                if rid not in seen:
                    seen.add(rid)
                    out.append(rid)
        finally:
            stack.discard(fp)
        return out

    def feature_pack_deps(
        self,
        feature_path: str,
        *,
        expand: bool = True,
    ) -> list[Dependency]:
        """Resolved mods.yml packs for one feature (deps-first when expand)."""
        pack_by_id = self.suggested_by_id()
        seeds = self.feature_pack_ids(feature_path)
        if not expand:
            return [
                replace(pack_by_id[pid], tier="downloads", feature=feature_path)
                for pid in seeds
                if pid in pack_by_id
            ]

        install_ids: list[str] = []
        visiting: set[str] = set()
        done: set[str] = set()

        def visit(pid: str) -> None:
            if pid in done:
                return
            if pid in visiting:
                raise ValueError(f"requires cycle involving {pid!r}")
            pack = pack_by_id.get(pid)
            if pack is None:
                raise ValueError(f"unknown pack: {pid!r}")
            visiting.add(pid)
            for dep_id in pack.requires:
                visit(dep_id)
            for leaf in expand_pack_composition(pack_by_id, pid):
                if leaf != pid:
                    visit(leaf)
            visiting.discard(pid)
            done.add(pid)
            if pid not in install_ids:
                install_ids.append(pid)

        for seed in seeds:
            for leaf in expand_pack_composition(pack_by_id, seed):
                visit(leaf)

        # Drop pure composition nodes (same policy as resolve_install_order)
        final_ids: list[str] = []
        for pid in install_ids:
            pack = pack_by_id[pid]
            if is_wizard_radio_parent(pack):
                continue
            if (
                pack.requires
                and not pack.has_remote_links()
                and not (
                    pack.disables
                    or pack.deletes
                    or pack.moves
                    or pack.console
                    or pack.mcm
                    or pack.settings
                    or pack.resets
                    or pack.enables
                )
            ):
                continue
            final_ids.append(pid)

        return [
            replace(pack_by_id[pid], tier="downloads", feature=feature_path)
            for pid in final_ids
            if pid in pack_by_id
        ]

    def feature_downloads(
        self,
        min_stage: str | int = "local",
        *,
        installed: set[str] | None = None,
    ) -> list[Dependency]:
        """Packs required by common + active installed features (dedupe by id)."""
        min_stage = parse_stage(min_stage)
        seen: set[str] = set()
        out: list[Dependency] = []
        for feat in ("common", *sorted(k for k in self.features if k != "common")):
            meta = self.features.get(feat)
            if not meta or not _feature_is_active(
                meta, min_stage, installed=installed
            ):
                continue
            if not meta.requires:
                continue
            for dep in self.feature_pack_deps(feat):
                if dep.id in seen:
                    continue
                seen.add(dep.id)
                out.append(dep)
        return out

    def collect_defaults(
        self,
        *,
        min_stage: str | int = "local",
        installed: set[str] | None = None,
        include_suggested: bool = True,
        mo2_root: Path | None = None,
        modlist: Path | None = None,
        suggested_ids: set[str] | None = None,
    ) -> list[InitSetting]:
        """resets (script def) → mcm → settings for active packs.

        Manual (no url:) packs only contribute when that mod is installed
        and enabled. ``suggested_ids`` limits which suggested packs apply.
        """
        min_stage = parse_stage(min_stage)
        out: list[InitSetting] = []
        reset_roots: list[tuple[str, str]] = []  # (source, root)

        def _dep_ok(dep: Dependency) -> bool:
            if (
                suggested_ids is not None
                and dep.tier == "suggested"
                and dep.id not in suggested_ids
            ):
                return False
            if not (dep.source == "user" or not dep.url):
                return True
            if mo2_root is None or modlist is None:
                return False
            return dep_effects_active(mo2_root, dep, modlist)

        for feat, meta in self.features.items():
            if not _feature_is_active(meta, min_stage, installed=installed):
                continue
            for root in meta.resets:
                reset_roots.append((feat, root))
            out.extend(
                _overrides_to_settings(feat, mcm=meta.mcm, settings=meta.settings)
            )
            for dep in self.feature_pack_deps(feat):
                if not _dep_ok(dep):
                    continue
                src = f"downloads:{dep.id}"
                for root in dep.resets:
                    reset_roots.append((src, root))
                out.extend(
                    _overrides_to_settings(src, mcm=dep.mcm, settings=dep.settings)
                )
        if include_suggested:
            for dep in self.suggested:
                if not _dep_ok(dep):
                    continue
                src = f"suggested:{dep.id}"
                for root in dep.resets:
                    reset_roots.append((src, root))
                out.extend(
                    _overrides_to_settings(src, mcm=dep.mcm, settings=dep.settings)
                )

        # Prepend reset InitSettings (script def=) so overrides win when applied
        # in order within apply_settings_to_axr_options (last write per key wins
        # only if we apply resets first in the list — apply walks list in order
        # and last change sticks). So: resets first, then overrides.
        if reset_roots and mo2_root is not None:
            script_defs = index_mcm_script_defaults(mo2_root)
            reset_settings: list[InitSetting] = []
            seen_keys: set[str] = set()
            for source, root in reset_roots:
                root_s = root.strip()
                if not root_s:
                    continue
                prefix = root_s.lower() + "/"
                for key, val in script_defs.items():
                    kl = key.lower()
                    if kl == root_s.lower() or kl.startswith(prefix):
                        if key.lower() in seen_keys:
                            continue
                        seen_keys.add(key.lower())
                        reset_settings.append(
                            InitSetting(f"reset:{source}", "mcm", key, val)
                        )
            out = reset_settings + out
        return out

    @property
    def requirements(self) -> list[Dependency]:
        """All feature requirements at dev+ (compat alias)."""
        return self.feature_downloads("dev")

    @property
    def mods(self) -> list[Dependency]:
        return self.feature_downloads("dev") + list(self.suggested)


def _parse_dep_url(raw, *, field: str) -> tuple[str, bool]:
    """Parse url:/buy_url: → (url_string, manual).

    Omitted / ``false`` / null / empty = no auto-download link (manual archive
    when used as ``url:``).
    """
    if raw is False or raw is None:
        return "", True
    if raw is True:
        raise ValueError(
            f"{field}: use a ModDB/GitHub URL (url:), a storefront URL (buy_url:), "
            f"or omit the key for a manual archive"
        )
    s = str(raw).strip()
    if not s or s.lower() in ("false", "null", "none", "manual", "-"):
        return "", True
    return s, False


def _validate_dep_link_roles(
    *,
    dep_id: str,
    section: str,
    url: str,
    manual: bool,
    buy_url: str,
    url_moddb: str = "",
    url_github: str = "",
    url_discord: str = "",
    url_download: str = "",
    url_kofi: str = "",
    url_patreon: str = "",
) -> None:
    """Validate typed url_* fields (and legacy url:/buy_url: mappings)."""
    if url and not manual:
        if not (
            is_moddb_url(url) or is_github_url(url) or is_discord_url(url)
        ):
            raise ValueError(
                f"{section}.{dep_id}.url: removed — use url_moddb:/url_github:/"
                f"url_discord:/url_download: (got {url!r})"
            )
    if url_moddb and not is_moddb_url(url_moddb):
        raise ValueError(
            f"{section}.{dep_id}.url_moddb: must be a ModDB link (got {url_moddb!r})"
        )
    if url_github and not is_github_url(url_github):
        raise ValueError(
            f"{section}.{dep_id}.url_github: must be a GitHub link (got {url_github!r})"
        )
    if url_discord and not is_discord_url(url_discord):
        raise ValueError(
            f"{section}.{dep_id}.url_discord: must be a Discord link "
            f"(got {url_discord!r})"
        )
    if url_download and not is_auto_download_url(url_download):
        raise ValueError(
            f"{section}.{dep_id}.url_download: must be a ModDB or GitHub download "
            f"link (got {url_download!r})"
        )
    if url_kofi and not is_kofi_url(url_kofi):
        raise ValueError(
            f"{section}.{dep_id}.url_kofi: must be a Ko-fi link (got {url_kofi!r})"
        )
    if url_patreon and not is_patreon_url(url_patreon):
        raise ValueError(
            f"{section}.{dep_id}.url_patreon: must be a Patreon link "
            f"(got {url_patreon!r})"
        )
    if buy_url:
        if is_moddb_url(buy_url) or is_github_url(buy_url) or is_discord_url(buy_url):
            raise ValueError(
                f"{section}.{dep_id}.buy_url: use url_kofi:/url_patreon: "
                f"(got {buy_url!r})"
            )


def _parse_name_list(raw, *, field: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{field} must be a list")
    return [str(x) for x in raw]


def _parse_conflict_list(item: dict, *, section: str) -> list[str]:
    """disables: preferred; legacy disabled: / disable: accepted."""
    if item.get("disables") is not None:
        return _parse_name_list(item.get("disables"), field=f"{section}.disables")
    if item.get("disabled") is not None:
        return _parse_name_list(item.get("disabled"), field=f"{section}.disabled")
    if item.get("disable") is not None:
        return _parse_name_list(item.get("disable"), field=f"{section}.disable")
    return []


def _parse_enable_list(item: dict, *, section: str) -> list[str]:
    if item.get("enables") is not None:
        return _parse_name_list(item.get("enables"), field=f"{section}.enables")
    if item.get("enable") is not None:
        return _parse_name_list(item.get("enable"), field=f"{section}.enable")
    return []


def _dep_from_mapping(
    dep_id: str,
    item: dict,
    *,
    tier: str,
    section: str,
    feature: str = "",
) -> Dependency:
    disables = _parse_conflict_list(item, section=f"{section}.{dep_id}")
    enables = _parse_enable_list(item, section=f"{section}.{dep_id}")
    effects = _pack_effect_fields(item, section=f"{section}.{dep_id}")
    if item.get("zip") is not None or item.get("file") is not None:
        raise ValueError(
            f"{section}.{dep_id}: zip:/file: removed — archive stem is the "
            f"block id (downloads/DOGMA/{dep_id}.zip|.7z|…)"
        )
    if item.get("label") is not None:
        raise ValueError(
            f"{section}.{dep_id}: label: removed — use the YAML block key as the id"
        )
    url, manual = _parse_dep_url(
        item.get("url"), field=f"{section}.{dep_id}.url"
    )
    buy_url, _buy_manual = _parse_dep_url(
        item.get("buy_url"), field=f"{section}.{dep_id}.buy_url"
    )
    url_moddb, _ = _parse_dep_url(
        item.get("url_moddb"), field=f"{section}.{dep_id}.url_moddb"
    )
    url_github, _ = _parse_dep_url(
        item.get("url_github"), field=f"{section}.{dep_id}.url_github"
    )
    url_discord, _ = _parse_dep_url(
        item.get("url_discord"), field=f"{section}.{dep_id}.url_discord"
    )
    url_download, _ = _parse_dep_url(
        item.get("url_download"), field=f"{section}.{dep_id}.url_download"
    )
    url_kofi, _ = _parse_dep_url(
        item.get("url_kofi"), field=f"{section}.{dep_id}.url_kofi"
    )
    url_patreon, _ = _parse_dep_url(
        item.get("url_patreon"), field=f"{section}.{dep_id}.url_patreon"
    )
    # Legacy url:/buy_url: → typed fields when omitted.
    if url and not manual:
        if is_discord_url(url):
            url_discord = url_discord or url
        elif is_moddb_url(url):
            url_moddb = url_moddb or url
            url_download = url_download or url
        elif is_github_url(url):
            url_github = url_github or url
            url_download = url_download or url
        else:
            url_download = url_download or url
    if buy_url:
        if is_kofi_url(buy_url):
            url_kofi = url_kofi or buy_url
        elif is_patreon_url(buy_url):
            url_patreon = url_patreon or buy_url
        else:
            # Unknown storefront — keep as Ko-fi slot for open-in-browser.
            url_kofi = url_kofi or buy_url
    # Canonical fields for older call sites.
    url = url_download
    buy_url = url_kofi or url_patreon
    manual = not bool(url_download)
    _validate_dep_link_roles(
        dep_id=dep_id,
        section=section,
        url=url,
        manual=manual,
        buy_url=buy_url,
        url_moddb=url_moddb,
        url_github=url_github,
        url_discord=url_discord,
        url_download=url_download,
        url_kofi=url_kofi,
        url_patreon=url_patreon,
    )
    path = str(item.get("path") or "").strip().replace("\\", "/")
    # stage: omit|dev|release — FOMOD + wizard gate. Legacy: fomod:, level:, wizard:.
    stage_raw = item.get("stage")
    if stage_raw is None:
        stage_raw = item.get("fomod")
    if stage_raw is None:
        stage_raw = item.get("level")
    stage = ""
    if stage_raw is not None and str(stage_raw).strip() != "":
        stage = parse_stage(stage_raw)
    else:
        wizard_raw = item.get("wizard")
        if wizard_raw is None:
            wizard_raw = item.get("installable")
        if isinstance(wizard_raw, bool):
            stage = "release" if wizard_raw else "omit"
        elif wizard_raw is not None and str(wizard_raw).strip() != "":
            flag = str(wizard_raw).strip().lower() in ("1", "true", "yes", "on")
            stage = "release" if flag else "omit"
    if path and not stage:
        stage = "release"
    has_links = bool(
        url_moddb
        or url_github
        or url_discord
        or url_download
        or url_kofi
        or url_patreon
        or buy_url
    )
    if path and has_links:
        raise ValueError(
            f"{section}.{dep_id}: use path: OR url_*: fields, not both"
        )
    source = str(item.get("source") or "").strip().lower()
    auto_dl = (url_download or "").strip()
    if path:
        # Local package zip (mo2/packages/<path_key>.zip) — not a downloads/DOGMA manual.
        source = source or "auto"
    elif (has_links and not auto_dl) or url_kofi or url_patreon:
        # Paid / Discord / page-only → user places the archive under downloads/DOGMA/.
        source = "user"
    elif not source:
        source = "auto"

    if item.get("selected") is not None:
        raise ValueError(
            f"{section}.{dep_id}: selected: removed — wizard defaults are "
            f"checked unless buy_url: is set (paid / manual purchase)"
        )

    if item.get("mods") is not None:
        raise ValueError(
            f"{section}.{dep_id}: mods: removed — use stage: release|dev "
            f"for a checkbox, or stage:+options: (no url) for a radio group"
        )

    # default: [mcm roots…] — wipe/reset those MCM namespaces to script defs.
    # (legacy resets: still accepted). Boolean default: is rejected.
    default_raw = item.get("default")
    if isinstance(default_raw, bool) or (
        isinstance(default_raw, str)
        and default_raw.strip().lower()
        in ("1", "true", "yes", "on", "0", "false", "no", "off")
    ):
        raise ValueError(
            f"{section}.{dep_id}: default: must be a list of MCM roots to "
            f"wipe/default (e.g. [idiots, video/weather]); wizard checkbox "
            f"defaults follow buy_url: (paid = off, else on)"
        )
    if default_raw is not None:
        resets = _parse_str_list(
            default_raw, field=f"{section}.{dep_id}.default"
        )
        if effects["resets"]:
            resets = _unique_strs([*resets, *effects["resets"]])
    else:
        resets = effects["resets"]

    # Unified requires: (legacy depends/dependencies still accepted).
    # Radio parents in the list become wizard_requires after materialize.
    req_parts: list[str] = []
    for req_key in ("requires", "requires_exclusive", "depends", "dependencies"):
        if item.get(req_key) is None:
            continue
        req_parts.extend(
            _parse_str_list(
                item.get(req_key),
                field=f"{section}.{dep_id}.{req_key}",
            )
        )
    requires_list = _unique_strs(req_parts)
    return Dependency(
        id=dep_id,
        tier=tier,
        url=url_download,
        buy_url=buy_url,
        url_moddb=url_moddb,
        url_github=url_github,
        url_discord=url_discord,
        url_download=url_download,
        url_kofi=url_kofi,
        url_patreon=url_patreon,
        path=path,
        stage=stage,
        archive_name=str(
            item.get("archive_name")
            or item.get("archive_stem")
            or item.get("archive")
            or ""
        ).strip(),
        source=source,
        howto=str(item.get("howto") or "").strip(),
        desc=str(item.get("desc") or item.get("description") or "").strip(),
        disables=disables,
        enables=enables,
        resets=resets,
        mcm=effects["mcm"],
        settings=effects["settings"],
        moves=effects["moves"],
        deletes=effects["deletes"],
        console=effects["console"],
        requires=requires_list,
        exclusive=str(item.get("exclusive") or "").strip(),
        group=str(item.get("group") or "").strip(),
        choice=str(item.get("choice") or "").strip(),
        options=[],
        option_groups=_parse_radio_option_groups(
            item.get("options"), field=f"{section}.{dep_id}.options"
        ),
        after_unpack=str(item.get("after_unpack") or "").strip(),
        feature=feature,
        wizard_requires=[],
    )


def _parse_external_list(
    raw_list,
    *,
    tier: str,
    section: str,
    feature: str = "",
) -> list[Dependency]:
    """Parse a YAML list of {id, ...} maps (legacy features.*.downloads list form)."""
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
    feature: str = "",
) -> list[Dependency]:
    """Parse a YAML mapping keyed by id (downloads: / suggested:)."""
    out: list[Dependency] = []
    if not raw_map:
        return out
    # Legacy list form: [{id: ...}, ...]
    if isinstance(raw_map, list):
        return _parse_external_list(
            raw_map, tier=tier, section=section, feature=feature
        )
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
            _dep_from_mapping(
                dep_id, meta, tier=tier, section=section, feature=feature
            )
        )
    return out


def _yaml_load_mapping(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"catalog not found: {path}")
    if not pyyaml_ok():
        raise RuntimeError("PyYAML not installed. Run DOGMA Setup once (installs PyYAML).")
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name} root must be a mapping")
    return raw


def _parse_features_block(feat_block: dict) -> dict[str, FeatureMeta]:
    """Parse features.yml → dict keyed by ``path`` (src/ id).

    YAML key is the display title. Each non-common block must set ``path:``.
    Legacy keys that look like ``category/feature`` (no ``path:``) are still
    accepted as the path, with the key also used as the title.
    """
    if not isinstance(feat_block, dict):
        raise ValueError("features: must be a mapping")
    features: dict[str, FeatureMeta] = {}
    titles_seen: dict[str, str] = {}  # title.lower → path

    for raw_key, meta in feat_block.items():
        key = str(raw_key).strip()
        if not key:
            continue
        is_common = key.lower() == "common"
        empty_fx = {
            "resets": [],
            "mcm": {},
            "settings": {},
            "moves": [],
            "deletes": [],
            "console": [],
        }
        feat_requires: list[str] = []
        title = key
        path = ""

        if meta is False:
            stage = "omit"
            disables, enables, fx = [], [], empty_fx
            path = "common" if is_common else key.replace("\\", "/")
        elif isinstance(meta, (int, str)) and not isinstance(meta, bool):
            stage = parse_stage(meta)
            disables, enables, fx = [], [], empty_fx
            path = "common" if is_common else key.replace("\\", "/")
        elif isinstance(meta, dict):
            path_raw = meta.get("path")
            if path_raw is not None and str(path_raw).strip():
                path = str(path_raw).strip().replace("\\", "/")
                title = key
            elif is_common:
                path = "common"
                title = "common"
            elif "/" in key.replace("\\", "/"):
                # Legacy: YAML key is the feature path
                path = key.replace("\\", "/")
                title = key
            else:
                raise ValueError(
                    f"features.{key!r}: missing path: "
                    f"(e.g. path: tooltips/weapons)"
                )
            if is_common:
                stage = parse_stage(meta.get("fomod", meta.get("stage", meta.get("level", "omit"))))
            elif "fomod" not in meta and "stage" not in meta and "level" not in meta:
                raise ValueError(
                    f"features.{title!r} ({path}): missing stage (omit|dev|release)"
                )
            else:
                stage = parse_stage(
                    meta.get("fomod", meta.get("stage", meta.get("level")))
                )
            disables = _parse_conflict_list(meta, section=f"features.{path}")
            enables = _parse_enable_list(meta, section=f"features.{path}")
            fx = _pack_effect_fields(meta, section=f"features.{path}")
            raw_dls = (
                meta.get("downloads")
                if meta.get("downloads") is not None
                else meta.get("requirements")
            )
            if raw_dls:
                nonempty = False
                if isinstance(raw_dls, dict) and raw_dls:
                    nonempty = True
                elif isinstance(raw_dls, list) and raw_dls:
                    nonempty = True
                if nonempty:
                    raise ValueError(
                        f"features.{title!r} ({path}): downloads:/requirements: "
                        f"moved to config/mods.yml — use requires: [Pack Id, …]"
                    )
            feat_req_parts: list[str] = []
            for req_key in ("requires", "depends", "dependencies"):
                if meta.get(req_key) is None:
                    continue
                feat_req_parts.extend(
                    _parse_str_list(
                        meta.get(req_key),
                        field=f"features.{path}.{req_key}",
                    )
                )
            feat_requires = _unique_strs(feat_req_parts)
        else:
            raise ValueError(f"features.{key!r}: want stage or mapping")

        path = path.strip().replace("\\", "/")
        if not path:
            raise ValueError(f"features.{key!r}: empty path")
        if path in features:
            raise ValueError(
                f"features: duplicate path {path!r} "
                f"({features[path].display_name!r} and {title!r})"
            )
        tkey = title.lower()
        if tkey in titles_seen and titles_seen[tkey] != path:
            raise ValueError(
                f"features: duplicate title {title!r} "
                f"(paths {titles_seen[tkey]!r} and {path!r})"
            )
        titles_seen[tkey] = path
        features[path] = FeatureMeta(
            path=path,
            stage=stage,
            title="" if is_common else title,
            disables=disables,
            enables=enables,
            resets=fx["resets"],
            mcm=fx["mcm"],
            settings=fx["settings"],
            moves=fx["moves"],
            deletes=fx["deletes"],
            console=fx["console"],
            requires=feat_requires,
            downloads=[],
        )
    return features


def missing_archives_for_selection(
    mo2_root: Path,
    data: ManifestData,
    selection: InstallerSelection,
) -> list[str]:
    """Pack ids that still need an archive for the chosen Install options/radios."""
    pack_by_id = data.suggested_by_id()
    missing: list[str] = []
    seen: set[str] = set()

    def _check(pack_id: str) -> None:
        for leaf in archive_leaves_for_pack(pack_by_id, pack_id):
            if leaf.id in seen:
                continue
            seen.add(leaf.id)
            path, _st = resolve_local_archive(mo2_root, leaf)
            if path is None:
                missing.append(leaf.id)

    for oid in selection.option_ids:
        _check(oid)
    for _group, pick in selection.exclusive_picks.items():
        if pick:
            _check(pick)
    return missing


def pack_needs_purchase(dep: Dependency) -> bool:
    """True when the user must buy/supply an archive (wizard default off)."""
    if dep.path or dep_auto_download_url(dep):
        return False
    return bool(dep.url_kofi or dep.url_patreon or dep.buy_url)


def option_default_selected(
    opt: InstallerOption, pack_by_id: dict[str, Dependency]
) -> bool:
    """Checked by default only when no archive field is required (path mods).

    Third-party packs default off; the wizard auto-checks when archives are linked.
    """
    for mid in opt.mods:
        pack = pack_by_id.get(mid)
        if pack is None:
            continue
        try:
            leaves = expand_pack_composition(pack_by_id, mid)
        except ValueError:
            leaves = [mid]
        for lid in leaves:
            leaf = pack_by_id.get(lid, pack if lid == mid else None)
            if leaf is not None and pack_needs_archive(leaf):
                return False
            if leaf is not None and pack_needs_purchase(leaf):
                return False
        if pack_needs_archive(pack) or pack_needs_purchase(pack):
            return False
    return True


def feature_installer_options(
    features: dict[str, FeatureMeta],
    suggested: list[Dependency],
    *,
    packages_dir: Path | None = None,
) -> list[InstallerOption]:
    """Wizard page-2 rows: non-common features that have a local package zip."""
    if packages_dir is None or not packages_dir.is_dir():
        return []
    by_id = {d.id: d for d in suggested}
    stub = ManifestData(path=Path("."), features=features, suggested=suggested)
    opts: list[InstallerOption] = []
    for feat, meta in features.items():
        if meta.always_on or meta.stage == "omit":
            continue
        zpath = packages_dir / f"{feature_path_key(feat)}.zip"
        if not zpath.is_file():
            continue
        seeds = stub.feature_pack_ids(feat) if meta.requires else []
        for sid in seeds:
            if sid not in by_id:
                raise ValueError(
                    f"feature {meta.display_name!r} ({feat}): pack {sid!r} "
                    f"missing from mods.yml"
                )
        opt = InstallerOption(
            id=feat,
            desc="",
            mods=list(seeds),
            default=True,
            requires=[],
        )
        opt.default = option_default_selected(opt, by_id)
        opts.append(opt)
    return opts


def apply_feature_option_defaults(
    data: ManifestData,
    installed: set[str] | None,
) -> None:
    """Path-mod defaults: D.O.G.M.A. features/tweaks start selected."""
    _ = installed  # kept for call-site compat; presence no longer clears defaults
    by_id = data.suggested_by_id()
    for opt in data.installer_options:
        dep = by_id.get(opt.id)
        if dep is None or not dep.path:
            continue
        opt.default = True


def features_from_deps(suggested: list[Dependency]) -> dict[str, FeatureMeta]:
    """Derive FeatureMeta map (keyed by path) from unified catalog path mods."""
    features: dict[str, FeatureMeta] = {}
    for dep in suggested:
        if dep.id.lower() == "common" and not dep.path:
            features["common"] = FeatureMeta(
                path="common",
                stage=dep.stage or "omit",
                title=dep.id,
                disables=list(dep.disables),
                enables=list(dep.enables),
                resets=list(dep.resets),
                mcm=dict(dep.mcm),
                settings=dict(dep.settings),
                moves=list(dep.moves),
                deletes=list(dep.deletes),
                console=list(dep.console),
                requires=list(dep.requires),
            )
            continue
        if not dep.path:
            continue
        path = dep.path.replace("\\", "/")
        stage = dep.stage or "omit"
        features[path] = FeatureMeta(
            path=path,
            stage=stage,
            title=dep.id,
            disables=list(dep.disables),
            enables=list(dep.enables),
            resets=list(dep.resets),
            mcm=dict(dep.mcm),
            settings=dict(dep.settings),
            moves=list(dep.moves),
            deletes=list(dep.deletes),
            console=list(dep.console),
            requires=list(dep.requires),
        )
    return features


def catalog_wizard_min_stage(mo2_root: Path | None = None) -> str:
    """Minimum stage for Setup wizard rows.

    Release installs (packages present) hide ``stage: dev`` entries.
    Local merge deploys (no packages) show ``dev`` + ``release``.
    Override with DOGMA_STAGE_MIN=omit|dev|release.
    """
    env = str(os.environ.get("DOGMA_STAGE_MIN") or "").strip()
    if env:
        return parse_stage(env)
    if mo2_root is None:
        return "dev"
    packages = feature_packages_dir(Path(mo2_root))
    if packages.is_dir() and any(packages.glob("*.zip")):
        return "release"
    return "dev"



def _apply_wizard_requires_from_requires(suggested: list[Dependency]) -> None:
    """Fill wizard_requires with radio-parent / exclusive entries from requires:."""
    by_id = {d.id: d for d in suggested}
    for dep in suggested:
        wiz: list[str] = []
        for rid in dep.requires:
            other = by_id.get(rid)
            if other is None:
                continue
            if is_wizard_radio_parent(other) or other.exclusive:
                wiz.append(rid)
        dep.wizard_requires = wiz


def wizard_options_from_deps(
    suggested: list[Dependency],
    *,
    min_stage: str = "dev",
) -> list[InstallerOption]:
    """Wizard checkboxes: every ``stage`` != omit pack/path mod (not radio parents)."""
    _apply_wizard_requires_from_requires(suggested)
    min_stage = parse_stage(min_stage)
    by_id = {d.id: d for d in suggested}
    opts: list[InstallerOption] = []
    for dep in suggested:
        if not dep.wizard or is_wizard_radio_parent(dep):
            continue
        if not stage_meets(dep.stage, min_stage):
            continue
        opt = InstallerOption(
            id=dep.id,
            desc=dep.desc,
            mods=installer_seed_ids(dep, by_id),
            default=True,
            requires=dep.wizard_requires,
        )
        opt.default = option_default_selected(opt, by_id)
        opts.append(opt)
    _validate_options_mods(opts, suggested)
    return opts


def is_wizard_tweak_pack(dep: Dependency) -> bool:
    """True for catalog Tweaks (``path: tweaks/...``), not e.g. mutants/tweaks."""
    path = (dep.path or "").replace("\\", "/").strip().lower()
    return path.startswith("tweaks/")


def wizard_pack_section_order(
    data: ManifestData,
    *,
    min_stage: str = "dev",
) -> list[tuple[str, str]]:
    """Radio groups + stage-gated pack/path checkboxes (all wizard pages)."""
    return [
        *wizard_page1_section_order(data, min_stage=min_stage),
        *wizard_page2_section_order(data, min_stage=min_stage),
        *wizard_page3_section_order(data, min_stage=min_stage),
    ]


def wizard_page1_section_order(
    data: ManifestData,
    *,
    min_stage: str = "dev",
) -> list[tuple[str, str]]:
    """Page 1: radio groups + third-party (non-path) checkboxes."""
    min_stage = parse_stage(min_stage)
    radios = wizard_radio_groups(data, min_stage=min_stage)
    sections: list[tuple[str, str]] = []
    by_id = data.suggested_by_id()

    for dep in data.suggested:
        if dep.id in radios:
            sections.append(("radio", dep.id))

    for dep in data.suggested:
        if is_wizard_radio_parent(dep):
            continue
        if not dep.wizard:
            continue
        if not stage_meets(dep.stage, min_stage):
            continue
        if dep.path:
            continue
        sections.append(("option", dep.id))

    # Legacy feature options without a path pack stay on page 1.
    feat_ids = set(data.features.keys())
    for opt in data.installer_options:
        if opt.id in feat_ids:
            continue
        if opt.id in by_id:
            continue
        sections.append(("option", opt.id))
    return sections


def wizard_page2_section_order(
    data: ManifestData,
    *,
    min_stage: str = "dev",
) -> list[tuple[str, str]]:
    """Page 2: D.O.G.M.A. features (non-tweak path mods)."""
    min_stage = parse_stage(min_stage)
    sections: list[tuple[str, str]] = []
    seen: set[str] = set()
    by_id = data.suggested_by_id()

    for dep in data.suggested:
        if is_wizard_radio_parent(dep):
            continue
        if not dep.wizard or not dep.path:
            continue
        if is_wizard_tweak_pack(dep):
            continue
        if not stage_meets(dep.stage, min_stage):
            continue
        sections.append(("option", dep.id))
        seen.add(dep.id)

    feat_ids = set(data.features.keys())
    for opt in data.installer_options:
        if opt.id not in feat_ids or opt.id in seen:
            continue
        # Path-keyed feature options under tweaks/ belong on page 3.
        feat = data.features.get(opt.id)
        path = (feat.path if feat is not None else opt.id).replace("\\", "/")
        if path.lower().startswith("tweaks/"):
            continue
        dep = by_id.get(opt.id)
        if dep is not None and is_wizard_tweak_pack(dep):
            continue
        sections.append(("option", opt.id))
        seen.add(opt.id)
    return sections


def wizard_page3_section_order(
    data: ManifestData,
    *,
    min_stage: str = "dev",
) -> list[tuple[str, str]]:
    """Page 3: D.O.G.M.A. Tweaks (``path: tweaks/...``)."""
    min_stage = parse_stage(min_stage)
    sections: list[tuple[str, str]] = []
    seen: set[str] = set()

    for dep in data.suggested:
        if is_wizard_radio_parent(dep):
            continue
        if not dep.wizard or not is_wizard_tweak_pack(dep):
            continue
        if not stage_meets(dep.stage, min_stage):
            continue
        sections.append(("option", dep.id))
        seen.add(dep.id)

    feat_ids = set(data.features.keys())
    for opt in data.installer_options:
        if opt.id in seen:
            continue
        feat = data.features.get(opt.id)
        if feat is None:
            continue
        path = feat.path.replace("\\", "/").lower()
        if not path.startswith("tweaks/"):
            continue
        sections.append(("option", opt.id))
        seen.add(opt.id)
    return sections


def wizard_feature_section_order(
    data: ManifestData,
) -> list[tuple[str, str]]:
    """Legacy: path-keyed feature options (usually empty with unified catalog)."""
    feat_ids = set(data.features.keys())
    sections: list[tuple[str, str]] = []
    for opt in data.installer_options:
        if opt.id in feat_ids:
            sections.append(("option", opt.id))
    return sections


def wizard_section_order(
    data: ManifestData,
    *,
    min_stage: str = "dev",
) -> list[tuple[str, str]]:
    """All Setup wizard sections (pages 1–3)."""
    return [
        *wizard_page1_section_order(data, min_stage=min_stage),
        *wizard_page2_section_order(data, min_stage=min_stage),
        *wizard_page3_section_order(data, min_stage=min_stage),
    ]


def _parse_requires(raw, *, section: str) -> list[str]:
    """Parse requires: list of mods.yml radio-group pack ids."""
    if not raw:
        return []
    if isinstance(raw, dict):
        raise ValueError(
            f"{section}: requires: must be a list of mods.yml pack "
            f"ids (not a mapping) — e.g. [Screen Space Shaders]"
        )
    return _parse_str_list(raw, field=f"{section}.requires")


def _unique_strs(items: Iterable[str]) -> list[str]:
    """Order-preserving unique strings (case-insensitive)."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        s = str(raw).strip()
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _unique_moves(items: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for src, dest in items:
        key = (str(src).strip().lower(), str(dest).strip().lower())
        if key in seen:
            continue
        seen.add(key)
        out.append((str(src).strip(), str(dest).strip()))
    return out


def _unique_kv(items: Iterable[tuple[str, str]]) -> dict[str, str]:
    """First key wins (case-insensitive)."""
    out: dict[str, str] = {}
    seen: set[str] = set()
    for key, val in items:
        k = str(key).strip()
        if not k:
            continue
        kl = k.lower()
        if kl in seen:
            continue
        seen.add(kl)
        out[k] = str(val)
    return out


def _parse_installer_options(raw, *, section: str = "installer_options") -> list[InstallerOption]:
    if not raw:
        return []
    if not isinstance(raw, dict):
        raise ValueError(f"{section}: must be a mapping keyed by option name")
    out: list[InstallerOption] = []
    for key, meta in raw.items():
        opt_id = str(key).strip()
        if not opt_id:
            continue
        if meta is None:
            meta = {}
        if not isinstance(meta, dict):
            raise ValueError(f"{section}.{opt_id}: want a mapping")
        if meta.get("exclusive") is not None:
            raise ValueError(
                f"{section}.{opt_id}: exclusive: on options removed — put "
                f"wizard:/options: (or legacy exclusive:) on packs in "
                f"mods.yml, and use requires: [Pack Id] here"
            )
        mods = _parse_str_list(meta.get("mods"), field=f"{section}.{opt_id}.mods")
        if not mods:
            raise ValueError(f"{section}.{opt_id}: mods: list is required")
        default = meta.get("default", False)
        if not isinstance(default, bool):
            default = str(default).strip().lower() in ("1", "true", "yes", "on")
        desc = str(meta.get("desc") or meta.get("description") or "").strip()
        req = _parse_requires(
            meta.get("requires")
            if meta.get("requires") is not None
            else meta.get("requires_exclusive"),
            section=f"{section}.{opt_id}",
        )
        out.append(
            InstallerOption(
                id=opt_id,
                desc=desc,
                mods=mods,
                default=default,
                requires=req,
            )
        )
    return out


def _parse_options_file(raw: dict, *, source: str = "options.yml") -> list[InstallerOption]:
    """Legacy parser for config/options.yml (no longer primary)."""
    if not isinstance(raw, dict):
        raise ValueError(f"{source}: root must be a mapping")
    if "installer_options" in raw:
        return _parse_installer_options(
            raw.get("installer_options"), section="installer_options"
        )
    return _parse_installer_options(raw, section=source)


def _parse_mods_file(raw: dict, *, source: str = "mods.yml") -> list[Dependency]:
    """Parse mods.yml: bare pack map or suggested_mods: / legacy wrappers."""
    if not isinstance(raw, dict):
        raise ValueError(f"{source}: root must be a mapping")
    if "suggested_mods" in raw:
        mods_raw = raw.get("suggested_mods") or {}
        if not isinstance(mods_raw, dict):
            raise ValueError(f"{source}: suggested_mods: must be a mapping")
        deps = _parse_external_map(
            mods_raw, tier="suggested", section="suggested_mods"
        )
        return _materialize_inline_radio_compositions(deps)
    if set(raw.keys()) <= {"suggested", "suggestions"}:
        sug_block = raw.get("suggested") or raw.get("suggestions") or {}
        if not isinstance(sug_block, dict):
            raise ValueError(f"{source}: suggested packs must be a mapping")
        deps = _parse_external_map(
            sug_block, tier="suggested", section="suggestions"
        )
        return _materialize_inline_radio_compositions(deps)
    deps = _parse_external_map(raw, tier="suggested", section=source)
    return _materialize_inline_radio_compositions(deps)


def is_wizard_radio_parent(dep: Dependency) -> bool:
    """``stage`` != omit + ``options:`` with no url/buy_url/path → radio section."""
    has_options = bool(dep.options or dep.option_groups)
    return bool(
        dep.wizard
        and has_options
        and not dep.has_remote_links()
        and not dep.path
    )


def installer_seed_ids(
    dep: Dependency, pack_by_id: dict[str, Dependency]
) -> list[str]:
    """Pack ids to install when a stage-gated wizard checkbox is selected.

    Checkbox packs install themselves; companions come from ``requires:``.
    """
    del pack_by_id  # reserved for future expansion; seeds are the pack itself
    return [dep.id]


def _validate_mods_composition(suggested: list[Dependency]) -> None:
    """Validate wizard:/options:/requires: references and shape.

    A pack is either:
      - wizard mod details (url/buy_url; optional requires:), or
      - a wizard radio group (options: + no url/buy_url), or
      - a composition choice (requires: + no url; listed under a radio group).
    ``options:`` is only for radio groups. Multi-pack option entries are
    materialized into omit composition packs before this runs.
    """
    by_id = {d.id: d for d in suggested}
    for dep in suggested:
        if (dep.options or dep.option_groups) and not is_wizard_radio_parent(dep):
            raise ValueError(
                f"mods.{dep.id}: options: is only for radio groups "
                f"(stage: release|dev, no url/buy_url) — use requires: for "
                f"composition, or list several packs under one options: entry"
            )
        if dep.wizard and (dep.options or dep.option_groups) and dep.has_remote_links():
            raise ValueError(
                f"mods.{dep.id}: wizard radio groups must not have url_*/buy_url:"
            )
        for oid in dep.options:
            if oid not in by_id:
                raise ValueError(
                    f"mods.{dep.id}: options entry {oid!r} missing from mods.yml"
                )
            if oid == dep.id:
                raise ValueError(f"mods.{dep.id}: options: cannot reference itself")
        for group in dep.option_groups:
            for oid in group:
                if oid not in by_id:
                    raise ValueError(
                        f"mods.{dep.id}: options entry {oid!r} missing from mods.yml"
                    )
                if oid == dep.id:
                    raise ValueError(
                        f"mods.{dep.id}: options: cannot reference itself"
                    )
        for dep_id in dep.requires:
            if dep_id not in by_id:
                raise ValueError(
                    f"mods.{dep.id}: requires entry {dep_id!r} missing from mods.yml"
                )
            if dep_id == dep.id:
                raise ValueError(f"mods.{dep.id}: requires: cannot reference itself")


def _validate_options_mods(
    opts: list[InstallerOption], suggested: list[Dependency]
) -> None:
    by_id = {d.id: d for d in suggested}
    _validate_mods_composition(suggested)
    for opt in opts:
        for mid in opt.mods:
            if mid not in by_id:
                raise ValueError(
                    f"options.{opt.id}: mods entry {mid!r} missing from mods.yml"
                )
        for pack_id in opt.requires:
            if pack_id not in by_id:
                raise ValueError(
                    f"options.{opt.id}: requires pack {pack_id!r} "
                    f"missing from mods.yml"
                )
            pack = by_id[pack_id]
            if is_wizard_radio_parent(pack):
                continue
            if pack.exclusive:
                continue
            raise ValueError(
                f"options.{opt.id}: requires pack {pack_id!r} "
                f"needs stage: release|dev + options: (or legacy exclusive:) "
                f"in mods.yml"
            )


def _parse_suggestions_file(raw: dict) -> tuple[list[InstallerOption], list[Dependency]]:
    """Parse legacy suggestions.yml: installer_options + suggested_mods (or flat map)."""
    if not isinstance(raw, dict):
        raise ValueError("suggestions.yml root must be a mapping")

    if "suggested_mods" in raw or "installer_options" in raw:
        opts = _parse_installer_options(
            raw.get("installer_options"), section="installer_options"
        )
        mods_raw = raw.get("suggested_mods")
        if mods_raw is None:
            mods_raw = {}
        if not isinstance(mods_raw, dict):
            raise ValueError("suggested_mods: must be a mapping")
        suggested = _parse_external_map(
            mods_raw, tier="suggested", section="suggested_mods"
        )
        _validate_options_mods(opts, suggested)
        return opts, suggested

    # Legacy: bare pack map (or suggested:/suggestions: wrapper)
    if set(raw.keys()) <= {"suggested", "suggestions"}:
        sug_block = raw.get("suggested") or raw.get("suggestions") or {}
    else:
        sug_block = raw
    suggested = _parse_external_map(
        sug_block, tier="suggested", section="suggestions"
    )
    _validate_mods_composition(suggested)
    return [], suggested


def _load_options_and_mods(
    cfg_dir: Path,
) -> tuple[list[InstallerOption], list[Dependency]]:
    """Load wizard options + pack catalog from manifest.yml or mods.yml."""
    # Prefer unified manifest when present alongside split files.
    for name in ("manifest.yml", "manifest.yaml"):
        man = cfg_dir / name
        if man.is_file():
            suggested = _parse_mods_file(
                _yaml_load_mapping(man), source=man.name
            )
            return wizard_options_from_deps(suggested), suggested

    mods_path = resolve_mods_path(cfg_dir)
    if mods_path is not None:
        mods_raw = _yaml_load_mapping(mods_path)
        suggested = _parse_mods_file(mods_raw, source=mods_path.name)
        return wizard_options_from_deps(suggested), suggested

    # Legacy: separate options.yml + mods.yml
    opts_path = resolve_options_path(cfg_dir)
    if opts_path is not None:
        # Without mods.yml we can only support legacy suggestions.yml.
        pass

    # Legacy combined file may hold both; prefer suggestions.yml.
    sug_path = resolve_suggestions_path(cfg_dir)
    if sug_path is not None:
        return _parse_suggestions_file(_yaml_load_mapping(sug_path))
    return [], []

def exclusive_pack_groups(data: ManifestData) -> dict[str, list[Dependency]]:
    """Legacy exclusive: group id → packs (stable mods.yml order)."""
    groups: dict[str, list[Dependency]] = {}
    for dep in data.suggested:
        if not dep.exclusive:
            continue
        groups.setdefault(dep.exclusive, []).append(dep)
    return groups


def wizard_radio_groups(
    data: ManifestData,
    *,
    min_stage: str = "dev",
) -> dict[str, list[Dependency]]:
    """Wizard radio sections: group key → choice packs.

    Compositional parents (``stage`` != omit + ``options:``) use the parent
    pack id as the key. Legacy ``exclusive:`` packs use the exclusive tag.
    """
    min_stage = parse_stage(min_stage)
    by_id = data.suggested_by_id()
    groups: dict[str, list[Dependency]] = {}
    for dep in data.suggested:
        if not is_wizard_radio_parent(dep):
            continue
        if not stage_meets(dep.stage, min_stage):
            continue
        choices: list[Dependency] = []
        for oid in dep.options:
            child = by_id.get(oid)
            if child is None:
                raise ValueError(
                    f"mods.{dep.id}: options entry {oid!r} missing from mods.yml"
                )
            choices.append(child)
        groups[dep.id] = choices
    for ex, packs in exclusive_pack_groups(data).items():
        if ex in groups:
            raise ValueError(
                f"exclusive group {ex!r} conflicts with wizard pack id {ex!r}"
            )
        groups[ex] = packs
    return groups


def radio_group_title(
    group: str, packs: list[Dependency], *, parent: Dependency | None = None
) -> str:
    if parent is not None:
        if parent.group:
            return parent.group
        return parent.id
    for p in packs:
        if p.group:
            return p.group
    ids = [p.id for p in packs]
    if len(ids) >= 2:
        parts = [i.split() for i in ids]
        common: list[str] = []
        for words in zip(*parts):
            if len(set(w.lower() for w in words)) == 1:
                common.append(words[0])
            else:
                break
        if common:
            return " ".join(common)
    return group.replace("_", " ").replace("-", " ").title() or "Options"


# Compat alias for older call sites
exclusive_group_title = radio_group_title


def expand_pack_composition(
    pack_by_id: dict[str, Dependency],
    pack_id: str,
    *,
    _stack: set[str] | None = None,
) -> list[str]:
    """Expand a radio choice to leaf install ids (unique, order-preserving).

    Pure composition nodes (no url/buy_url/path, not a radio parent) expand via
    ``requires:``. Downloadable / path packs are leaves (their ``requires:`` are
    still walked later by the install-order visitor).
    """
    stack = _stack if _stack is not None else set()
    pid = str(pack_id).strip()
    if not pid:
        return []
    if pid in stack:
        raise ValueError(f"requires: cycle involving {pid!r}")
    pack = pack_by_id.get(pid)
    if pack is None:
        raise ValueError(f"unknown pack: {pid!r}")
    # Composition-only: requires lists the packs this choice installs
    if (
        pack.requires
        and not pack.has_remote_links()
        and not pack.path
        and not is_wizard_radio_parent(pack)
    ):
        stack.add(pid)
        out: list[str] = []
        seen: set[str] = set()
        try:
            for dep_id in pack.requires:
                for leaf in expand_pack_composition(
                    pack_by_id, dep_id, _stack=stack
                ):
                    if leaf not in seen:
                        seen.add(leaf)
                        out.append(leaf)
        finally:
            stack.discard(pid)
        return out
    return [pid]


# Compat alias
expand_pack_options = expand_pack_composition


def _merge_effect_fields(*deps: Dependency) -> dict:
    """Combine effect lists/maps from deps into unique fields."""
    disables: list[str] = []
    enables: list[str] = []
    deletes: list[str] = []
    console: list[str] = []
    resets: list[str] = []
    mcm_pairs: list[tuple[str, str]] = []
    settings_pairs: list[tuple[str, str]] = []
    for d in deps:
        disables.extend(d.disables)
        enables.extend(d.enables)
        deletes.extend(d.deletes)
        console.extend(d.console)
        resets.extend(d.resets)
        mcm_pairs.extend(d.mcm.items())
        settings_pairs.extend(d.settings.items())
    return {
        "disables": _unique_strs(disables),
        "enables": _unique_strs(enables),
        "deletes": _unique_strs(deletes),
        "console": _unique_strs(console),
        "resets": _unique_strs(resets),
        "mcm": _unique_kv(mcm_pairs),
        "settings": _unique_kv(settings_pairs),
    }


def uniquify_dep_effects(deps: list[Dependency]) -> list[Dependency]:
    """Copy deps with shared effect values unique across the list (first wins)."""
    seen_disables: set[str] = set()
    seen_enables: set[str] = set()
    seen_deletes: set[str] = set()
    seen_console: set[str] = set()
    seen_resets: set[str] = set()
    seen_mcm: set[str] = set()
    seen_settings: set[str] = set()
    out: list[Dependency] = []
    for dep in deps:
        disables: list[str] = []
        for x in dep.disables:
            k = x.lower()
            if k in seen_disables:
                continue
            seen_disables.add(k)
            disables.append(x)
        enables: list[str] = []
        for x in dep.enables:
            k = x.lower()
            if k in seen_enables:
                continue
            seen_enables.add(k)
            enables.append(x)
        deletes: list[str] = []
        for x in dep.deletes:
            k = x.lower()
            if k in seen_deletes:
                continue
            seen_deletes.add(k)
            deletes.append(x)
        console: list[str] = []
        for x in dep.console:
            k = x.lower()
            if k in seen_console:
                continue
            seen_console.add(k)
            console.append(x)
        resets: list[str] = []
        for x in dep.resets:
            k = x.lower()
            if k in seen_resets:
                continue
            seen_resets.add(k)
            resets.append(x)
        mcm: dict[str, str] = {}
        for k, v in dep.mcm.items():
            kl = k.lower()
            if kl in seen_mcm:
                continue
            seen_mcm.add(kl)
            mcm[k] = v
        settings: dict[str, str] = {}
        for k, v in dep.settings.items():
            kl = k.lower()
            if kl in seen_settings:
                continue
            seen_settings.add(kl)
            settings[k] = v
        # Important: keep cross-pack move ordering semantics.
        # Deduping moves globally would prevent later packs from overwriting
        # the same destination with their own content.
        moves: list[tuple[str, str]] = _unique_moves(dep.moves)
        out.append(
            replace(
                dep,
                disables=disables,
                enables=enables,
                deletes=deletes,
                console=console,
                resets=resets,
                mcm=mcm,
                settings=settings,
                moves=moves,
            )
        )
    return out


def required_exclusive_groups(
    data: ManifestData, option_ids: Iterable[str]
) -> dict[str, str]:
    """Radio groups required by selected options → preferred default choice id."""
    opt_by_id = {o.id: o for o in data.installer_options}
    pack_by_id = data.suggested_by_id()
    out: dict[str, str] = {}
    for oid in option_ids:
        opt = opt_by_id.get(str(oid).strip())
        if not opt:
            continue
        for pack_id in opt.requires:
            pack = pack_by_id.get(pack_id)
            if not pack:
                continue
            if is_wizard_radio_parent(pack):
                # Preferred default = first radio choice
                preferred = pack.options[0]
                out.setdefault(pack.id, preferred)
            elif pack.exclusive:
                out.setdefault(pack.exclusive, pack_id)
    return out


def default_exclusive_picks(
    data: ManifestData, option_ids: Iterable[str] | None = None
) -> dict[str, str]:
    """Exclusive picks for default/NO_WIZARD installs (required groups only)."""
    ids = (
        list(option_ids)
        if option_ids is not None
        else default_installer_option_ids(data)
    )
    return dict(required_exclusive_groups(data, ids))


def resolve_install_order(
    data: ManifestData,
    selected_option_ids: Iterable[str],
    exclusive_picks: dict[str, str] | None = None,
    *,
    installed: set[str] | None = None,
) -> list[Dependency]:
    """Expand installer_options + radio picks → deps-first install list.

    ``exclusive_picks`` maps radio group key → chosen pack id (or \"\" for none).
    Compositional picks expand ``options:`` and merge parent+choice effects uniquely.

    ``installed``: also seed pack requires for DOGMA features already in the mod
    (e.g. FOMOD-installed Fast Travel still pulls Tarkov when unchecked on page 2).
    """
    opt_by_id = {o.id: o for o in data.installer_options}
    pack_by_id = data.suggested_by_id()
    radio_groups = wizard_radio_groups(data)
    selected = [str(x).strip() for x in selected_option_ids if str(x).strip()]
    for oid in selected:
        if oid not in opt_by_id:
            raise ValueError(f"unknown installer option: {oid!r}")

    picks = {
        str(g).strip(): str(p or "").strip()
        for g, p in (exclusive_picks or {}).items()
        if str(g).strip()
    }

    required = required_exclusive_groups(data, selected)
    for group, default_pack in required.items():
        if not picks.get(group):
            picks[group] = default_pack

    for group, pack_id in list(picks.items()):
        if not pack_id:
            if group in required:
                choices = radio_groups.get(group, [])
                raise ValueError(
                    f"option group {group!r} is required by selected options "
                    f"— pick one of: {', '.join(p.id for p in choices)}"
                )
            continue
        if group not in radio_groups:
            raise ValueError(f"unknown option group: {group!r}")
        allowed = {p.id for p in radio_groups[group]}
        if pack_id not in allowed:
            raise ValueError(
                f"pack {pack_id!r} is not a choice in option group {group!r}"
            )

    # Seeds from checkbox mods and radio picks (expand requires: composition)
    seeds: list[str] = []
    seen_seed: set[str] = set()
    # Extra effect packs to fold onto the first leaf of each radio pick
    pick_overlays: dict[str, list[Dependency]] = {}  # first_leaf_id → extras

    def _add_seed(mid: str) -> None:
        if mid in seen_seed:
            return
        seen_seed.add(mid)
        seeds.append(mid)

    for oid in selected:
        for mid in opt_by_id[oid].mods:
            pack = pack_by_id.get(mid)
            if pack is None:
                raise ValueError(f"unknown suggested_mods pack: {mid!r}")
            # Skip pure radio parents listed on mods: by mistake
            if is_wizard_radio_parent(pack):
                continue
            _add_seed(mid)

    # Pack requires for features already merged (FOMOD) even if page-2 unchecked.
    if installed:
        low_inst = {x.lower() for x in installed}
        for feat, meta in data.features.items():
            if meta.always_on or meta.stage == "omit":
                continue
            if feat not in installed and feat.lower() not in low_inst:
                continue
            if not meta.requires:
                continue
            for mid in data.feature_pack_ids(feat):
                pack = pack_by_id.get(mid)
                if pack is None or is_wizard_radio_parent(pack):
                    continue
                _add_seed(mid)

    for group, choice_id in picks.items():
        if not choice_id:
            continue
        leaf_ids = expand_pack_composition(pack_by_id, choice_id)
        if not leaf_ids:
            continue
        for leaf_id in leaf_ids:
            _add_seed(leaf_id)
        extras: list[Dependency] = []
        parent = pack_by_id.get(group)
        if parent is not None and is_wizard_radio_parent(parent):
            extras.append(parent)
        choice = pack_by_id[choice_id]
        # Composition node (requires expand away from self) contributes its fields
        if choice.id not in leaf_ids:
            extras.append(choice)
        if extras:
            pick_overlays.setdefault(leaf_ids[0], []).extend(extras)

    # Collapse legacy exclusive: among seeds sharing a tag
    preferred_pack = {g: p for g, p in picks.items() if p}
    by_pack_ex: dict[str, list[str]] = {}
    for mid in seeds:
        pack = pack_by_id.get(mid)
        if not pack or not pack.exclusive:
            continue
        by_pack_ex.setdefault(pack.exclusive, []).append(mid)
    drop: set[str] = set()
    for ex, mids in by_pack_ex.items():
        if len(mids) <= 1:
            continue
        winner = preferred_pack.get(ex)
        if winner not in mids:
            winner = mids[-1]
        for mid in mids:
            if mid != winner:
                drop.add(mid)
    if drop:
        seeds = [m for m in seeds if m not in drop]

    visiting: set[str] = set()
    done: set[str] = set()
    ordered: list[str] = []

    def visit(mid: str) -> None:
        if mid in done:
            return
        if mid in visiting:
            raise ValueError(f"requires cycle involving {mid!r}")
        if mid not in pack_by_id:
            raise ValueError(f"unknown suggested_mods pack: {mid!r}")
        visiting.add(mid)
        for dep_id in pack_by_id[mid].requires:
            visit(dep_id)
        visiting.remove(mid)
        done.add(mid)
        ordered.append(mid)

    for mid in seeds:
        visit(mid)

    by_pack_ex2: dict[str, list[str]] = {}
    for mid in ordered:
        pack = pack_by_id[mid]
        if pack.exclusive:
            by_pack_ex2.setdefault(pack.exclusive, []).append(mid)
    drop2: set[str] = set()
    for ex, mids in by_pack_ex2.items():
        if len(mids) <= 1:
            continue
        winner = preferred_pack.get(ex)
        if winner not in mids:
            winner = mids[-1]
        for mid in mids:
            if mid != winner:
                drop2.add(mid)
    if drop2:
        ordered = [m for m in ordered if m not in drop2]

    # Install units = ordered packs that do work (not pure option parents /
    # composition-only nodes whose children were already expanded into seeds).
    install_ids: list[str] = []
    for mid in ordered:
        pack = pack_by_id[mid]
        if is_wizard_radio_parent(pack):
            continue
        if (
            pack.options
            and not pack.has_remote_links()
            and not pack.path
            and not pack.enables
        ):
            if not (
                pack.disables
                or pack.deletes
                or pack.moves
                or pack.console
                or pack.mcm
                or pack.settings
                or pack.resets
            ):
                continue
        # Pure composition choice already expanded into requires leaves
        if (
            pack.requires
            and not pack.has_remote_links()
            and not pack.path
            and not is_wizard_radio_parent(pack)
            and not (
                pack.disables
                or pack.deletes
                or pack.moves
                or pack.console
                or pack.mcm
                or pack.settings
                or pack.resets
            )
        ):
            continue
        install_ids.append(mid)

    # Fold parent/composite effects onto the first leaf of each pick, then
    # uniquify shared lists across the whole install order.
    by_id_copies: dict[str, Dependency] = {
        mid: replace(pack_by_id[mid]) for mid in install_ids
    }
    for leaf_id, extras in pick_overlays.items():
        if leaf_id not in by_id_copies:
            continue
        base = by_id_copies[leaf_id]
        merged = _merge_effect_fields(base, *extras)
        by_id_copies[leaf_id] = replace(
            base,
            disables=merged["disables"],
            enables=merged["enables"],
            deletes=merged["deletes"],
            console=merged["console"],
            resets=merged["resets"],
            mcm=merged["mcm"],
            settings=merged["settings"],
            # keep this leaf's moves (extras rarely have moves)
            moves=_unique_moves([*base.moves, *(m for e in extras for m in e.moves)]),
        )

    return uniquify_dep_effects([by_id_copies[mid] for mid in install_ids])


def default_installer_option_ids(
    data: ManifestData,
    *,
    installed: set[str] | None = None,
) -> list[str]:
    """Options checked by default (packs: no buy_url; features: not yet installed)."""
    if installed is not None:
        apply_feature_option_defaults(data, installed)
    return [o.id for o in data.installer_options if o.default]


def selection_path(mo2_root: Path) -> Path:
    return mo2_tools_dir(mo2_root) / "config" / "selection.json"


def save_installer_selection(
    mo2_root: Path,
    selection: InstallerSelection | list[str],
    exclusive_picks: dict[str, str] | None = None,
) -> Path:
    if isinstance(selection, InstallerSelection):
        option_ids = list(selection.option_ids)
        picks = dict(selection.exclusive_picks)
        features_chosen = bool(selection.features_chosen)
    else:
        option_ids = list(selection)
        picks = dict(exclusive_picks or {})
        features_chosen = False
    path = selection_path(mo2_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "installer_options": option_ids,
        "exclusive": picks,
        "features_chosen": features_chosen,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def load_installer_selection(mo2_root: Path) -> InstallerSelection | None:
    path = selection_path(mo2_root)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    opts = raw.get("installer_options")
    if not isinstance(opts, list):
        return None
    option_ids = [str(x).strip() for x in opts if str(x).strip()]
    picks_raw = raw.get("exclusive") if isinstance(raw.get("exclusive"), dict) else {}
    picks = {
        str(g).strip(): str(p or "").strip()
        for g, p in picks_raw.items()
        if str(g).strip()
    }
    features_chosen = bool(raw.get("features_chosen"))
    return InstallerSelection(
        option_ids=option_ids,
        exclusive_picks=picks,
        features_chosen=features_chosen,
    )


def refresh_feature_package_options(
    data: ManifestData,
    mo2_root: Path,
) -> None:
    """Rebuild page-2 feature options from live mo2/packages/ zips."""
    packages = resolve_feature_packages_dir(mo2_root=mo2_root)
    pack_opts = [o for o in data.installer_options if o.id not in data.features]
    feat_opts = feature_installer_options(
        data.features, data.suggested, packages_dir=packages
    )
    data.installer_options = [*pack_opts, *feat_opts]


def sanitize_installer_selection(
    data: ManifestData,
    selection: InstallerSelection,
) -> InstallerSelection:
    """Drop unknown option ids (stale selection / removed packages) with a warn."""
    known = {o.id for o in data.installer_options}
    kept: list[str] = []
    for oid in selection.option_ids:
        if oid in known:
            kept.append(oid)
            continue
        warn(f"ignoring unknown installer option in selection: {oid!r}")
    return InstallerSelection(
        option_ids=kept,
        exclusive_picks=dict(selection.exclusive_picks),
        features_chosen=selection.features_chosen,
    )


def expand_feature_package_selection(
    data: ManifestData,
    selected_option_ids: Iterable[str],
) -> list[str]:
    """Selected features plus nested feature requires (for package unpack)."""
    out: list[str] = []
    seen: set[str] = set()

    def visit(ref: str) -> None:
        fp = data.resolve_feature_path(ref)
        if not fp or fp in seen:
            return
        meta = data.features.get(fp)
        if meta is None or meta.always_on or meta.stage == "omit":
            return
        seen.add(fp)
        out.append(fp)
        for dep in meta.requires:
            nested = data.resolve_feature_path(dep)
            if nested is not None:
                visit(nested)

    for oid in selected_option_ids:
        if str(oid).strip() in data.features or data.resolve_feature_path(str(oid)):
            visit(str(oid))
    return out


def _parse_suggestions_block(raw_sug) -> list[Dependency]:
    suggested = _parse_external_map(
        raw_sug, tier="suggested", section="suggestions"
    )
    return suggested


def _manifest_from_parts(
    path: Path,
    features: dict[str, FeatureMeta],
    suggested: list[Dependency],
    installer_options: list[InstallerOption] | None = None,
    *,
    packages_dir: Path | None = None,
) -> ManifestData:
    # Validate feature requires: resolve early
    stub = ManifestData(path=path, features=features, suggested=suggested)
    for feat, meta in features.items():
        if not meta.requires:
            continue
        stub.feature_pack_ids(feat)

    opts = list(installer_options or [])
    # Path mods with stage:dev|release are already in opts via wizard_options_from_deps.
    # Only add legacy package-only rows when a zip exists and no wizard row yet.
    feat_opts = feature_installer_options(
        features, suggested, packages_dir=packages_dir
    )
    seen = {o.id for o in opts}
    for fo in feat_opts:
        if fo.id in seen:
            continue
        # Map path-id options to display title ids when possible
        meta = features.get(fo.id)
        if meta and meta.title and meta.title in seen:
            continue
        opts.append(fo)

    defaults: list[InitSetting] = []
    for feat in features.values():
        defaults.extend(
            _overrides_to_settings(feat.path, mcm=feat.mcm, settings=feat.settings)
        )
        if feat.requires:
            for dep in stub.feature_pack_deps(feat.path):
                defaults.extend(
                    _overrides_to_settings(
                        f"downloads:{dep.id}", mcm=dep.mcm, settings=dep.settings
                    )
                )
    for dep in suggested:
        defaults.extend(
            _overrides_to_settings(
                f"suggested:{dep.id}", mcm=dep.mcm, settings=dep.settings
            )
        )
    return ManifestData(
        path=path,
        features=features,
        suggested=suggested,
        installer_options=opts,
        defaults=defaults,
    )


def _features_map_from_file(raw: dict, *, source: str) -> dict:
    """Accept bare feature map, or wrapped {features: {...}}."""
    if raw.get("requirements") is not None:
        raise ValueError(
            f"{source}: top-level 'requirements:' removed — put packs in "
            "config/mods.yml and list them under features.<name>.requires:"
        )
    if raw.get("defaults") is not None:
        raise ValueError(
            f"{source}: top-level 'defaults:' removed — put resets: / mcm: / "
            "settings: on each feature / download / suggestion"
        )
    if "suggested" in raw or "suggestions" in raw:
        raise ValueError(
            f"{source}: put suggested packs in config/mods.yml "
            "(not inside features.yml)"
        )
    if "features" in raw and "common" not in raw:
        block = raw.get("features") or {}
        if not isinstance(block, dict):
            raise ValueError(f"{source}: features: must be a mapping")
        return block
    return raw


def _load_split_manifests(cfg_dir: Path, parts: list[Path]) -> ManifestData:
    """Merge split manifest-*.yml parts into one catalog."""
    suggested: list[Dependency] = []
    seen: set[str] = set()
    for part in parts:
        raw = _yaml_load_mapping(part)
        chunk = _parse_mods_file(raw, source=part.name)
        for dep in chunk:
            if dep.id in seen:
                raise ValueError(
                    f"duplicate pack id {dep.id!r} in {part.name} "
                    f"(already defined in an earlier split manifest)"
                )
            seen.add(dep.id)
            suggested.append(dep)
    if not suggested:
        raise ValueError(
            f"split manifests under {cfg_dir} contain no packs "
            f"({', '.join(p.name for p in parts)})"
        )
    features = features_from_deps(suggested)
    opts = wizard_options_from_deps(suggested)
    packages_dir = resolve_feature_packages_dir(catalog_path=parts[0])
    return _manifest_from_parts(
        parts[0],
        features,
        suggested,
        installer_options=opts,
        packages_dir=packages_dir,
    )


def load_manifest(path: Path) -> ManifestData:
    """Load catalog from split/unified manifest or features.yml + mods.yml.

    ``path`` may be the config directory, any ``manifest-*.yml`` split part,
    ``manifest.yml``, or ``features.yml``.
    """
    if path.is_dir():
        split = split_manifest_paths(path)
        if split:
            return _load_split_manifests(path, split)
        for name in (
            "manifest.yml",
            "manifest.yaml",
            "features.yml",
            "features.yaml",
        ):
            cand = path / name
            if cand.is_file():
                return load_manifest(cand)
        raise FileNotFoundError(
            f"split manifests, manifest.yml, or features.yml not found under {path}"
        )

    if not path.is_file():
        raise FileNotFoundError(f"catalog not found: {path}")

    name = path.name.lower()
    # Any part of the split catalog → load all parts from the config dir.
    if name.startswith("manifest-") and name.endswith((".yml", ".yaml")):
        split = split_manifest_paths(path.parent)
        if not split:
            raise FileNotFoundError(
                f"split manifests not found alongside {path.name} in {path.parent}"
            )
        return _load_split_manifests(path.parent, split)

    companion = (
        "suggestions.yml",
        "suggested.yml",
        "suggestions.yaml",
        "suggested.yaml",
        "options.yml",
        "options.yaml",
        "installer_options.yml",
        "mods.yml",
        "mods.yaml",
        "suggested_mods.yml",
    )
    if name in companion:
        try:
            return load_manifest(path.parent)
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"split manifests, manifest.yml, or features.yml "
                f"required alongside {path.name} (looked in {path.parent})"
            ) from exc

    raw = _yaml_load_mapping(path)
    packages_dir = resolve_feature_packages_dir(catalog_path=path)

    # Legacy unified catalog (url packs + path mods in one file)
    if name in ("manifest.yml", "manifest.yaml"):
        suggested = _parse_mods_file(raw, source=path.name)
        features = features_from_deps(suggested)
        opts = wizard_options_from_deps(suggested)
        return _manifest_from_parts(
            path,
            features,
            suggested,
            installer_options=opts,
            packages_dir=packages_dir,
        )

    # features.yml — path mods (+ sibling mods.yml / manifest via _load_options_and_mods)
    if name in ("features.yml", "features.yaml"):
        # Prefer sibling split/unified manifests when present (avoid recurse).
        split = split_manifest_paths(path.parent)
        if split:
            return _load_split_manifests(path.parent, split)
        for n in ("manifest.yml", "manifest.yaml"):
            man = path.parent / n
            if man.is_file():
                return load_manifest(man)
        feat_block = _features_map_from_file(raw, source=path.name)
        # New shape: same as mods (path/fomod/wizard) — parse as deps then derive
        try:
            path_deps = _parse_mods_file(raw, source=path.name)
            features = features_from_deps(path_deps)
        except ValueError:
            features = _parse_features_block(feat_block)
            path_deps = []
        installer_options, suggested = _load_options_and_mods(path.parent)
        # Merge path mods from features.yml into suggested if not already present
        by_id = {d.id: d for d in suggested}
        for d in path_deps:
            if d.id not in by_id:
                suggested.append(d)
                by_id[d.id] = d
        if not installer_options:
            installer_options = wizard_options_from_deps(suggested)
        if not features:
            features = features_from_deps(suggested)
        return _manifest_from_parts(
            path,
            features,
            suggested,
            installer_options=installer_options,
            packages_dir=packages_dir,
        )

    # Legacy unified manifest.yml with features:/suggested: wrappers
    if raw.get("requirements") is not None:
        raise ValueError(
            "manifest.yml top-level 'requirements:' removed — put packs in "
            "config/mods.yml and list them under features.<name>.requires:"
        )
    if raw.get("defaults"):
        raise ValueError(
            "manifest.yml top-level 'defaults:' removed — put resets: / mcm: / "
            "settings: under each feature / downloads.<id> / suggestion"
        )
    feat_block = raw.get("features") or {}
    features = _parse_features_block(feat_block)
    suggested = _parse_suggestions_block(
        raw.get("suggested") if raw.get("suggested") is not None else raw.get("suggestions")
    )
    if not suggested and (raw.get("mods") or raw.get("dependencies")):
        for item in raw.get("mods") or raw.get("dependencies") or []:
            if not isinstance(item, dict):
                continue
            tier = str(item.get("tier") or "suggested").strip().lower()
            if tier in ("required", "downloads"):
                raise ValueError(
                    "legacy mods with tier:required — put packs in mods.yml "
                    "and features.<name>.requires:"
                )
            suggested.extend(
                _parse_external_list([item], tier="suggested", section="mods")
            )
    return _manifest_from_parts(path, features, suggested)


def read_manifest_levels(path: Path) -> dict[str, int]:
    """Feature path → numeric rank (0/1/2). Accepts manifest.yml (or legacy .ini)."""
    if path.suffix.lower() in (".yml", ".yaml") or path.name in (
        "manifest.yml",
        "features.yml",
    ):
        data = load_manifest(path)
        return {k.lower(): STAGE_RANK[v.stage] for k, v in data.features.items()}
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
        levels[key.lower()] = STAGE_RANK[parse_stage(val)]
    return levels


def feature_disable_rules(
    data: ManifestData,
    *,
    min_stage: str | int = "local",
    installed: set[str] | None = None,
) -> tuple[list[Rule], list[str], list[str]]:
    min_stage = parse_stage(min_stage)
    merged: dict[tuple[str, str], list[str]] = {}
    patterns: dict[tuple[str, str], str] = {}
    active: list[str] = []
    skipped: list[str] = []

    for feat, meta in data.features.items():
        if not _feature_is_active(meta, min_stage, installed=installed):
            if meta.disables:
                skipped.append(feat)
            continue
        if not meta.disables:
            continue
        active.append(feat)
        for raw in meta.disables:
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
    min_stage: str | int = "local",
    installed: set[str] | None = None,
) -> tuple[list[Rule], list[str]]:
    """Rules for mods that should be enabled when the feature (or common) is active."""
    min_stage = parse_stage(min_stage)
    merged: dict[tuple[str, str], list[str]] = {}
    patterns: dict[tuple[str, str], str] = {}
    active: list[str] = []

    for feat, meta in data.features.items():
        if not _feature_is_active(meta, min_stage, installed=installed):
            continue
        if not meta.enables:
            continue
        active.append(feat)
        for raw in meta.enables:
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
        info(f"modlist: wrote {modlist} ({len(result.enabled)} newly enabled)")
    return result


def read_disable_ini(
    path: Path,
    manifest_path: Path,
    *,
    min_stage: str | int = "local",
) -> tuple[list[Rule], list[str], list[str]]:
    """Legacy name: prefer unified manifest.yml when manifest_path is .yml."""
    if manifest_path.suffix.lower() in (".yml", ".yaml"):
        return feature_disable_rules(load_manifest(manifest_path), min_stage=min_stage)
    # Fallback: old dual-ini path
    if not path.is_file():
        raise FileNotFoundError(f"disabled.ini not found: {path}")

    min_rank = LEVEL_RANK[parse_stage(min_stage)]
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


def enabled_mods_matching_disables(
    enabled_names: Iterable[str],
    disable_patterns: Iterable[str],
) -> list[str]:
    """Enabled modlist names that match any disable pattern (would be turned off)."""
    rules = rules_from_disable_names(disable_patterns, source="preview")
    if not rules:
        return []
    hit = [n for n in enabled_names if mod_matches(n, rules)]
    return sorted(set(hit), key=lambda s: s.lower())


def preview_disables_for_deps(
    deps: Iterable[Dependency],
    enabled_names: Iterable[str],
) -> list[str]:
    """Currently-enabled mods that these packs' disables: would turn off."""
    patterns: list[str] = []
    for dep in deps:
        patterns.extend(dep.disables)
    return enabled_mods_matching_disables(enabled_names, patterns)


def mods_matching_patterns(
    names: Iterable[str],
    patterns: Iterable[str],
) -> list[str]:
    """Modlist names that match any disable/enable-style pattern."""
    return enabled_mods_matching_disables(names, patterns)


def preview_tweak_packs(deps: Iterable[Dependency]) -> list[str]:
    """Pack ids that apply MCM / settings / resets (config tweaks)."""
    return [d.id for d in deps if d.has_axr_effects()]


def format_effect_lists_plain(sections: list[tuple[str, list[str]]]) -> str:
    """Plain-text effect lists (FOMOD desc / tooltips): blank line between lists."""
    blocks: list[str] = []
    for label, items in sections:
        if not items:
            continue
        lines = [f"{label}:"] + [f"  • {item}" for item in items]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def preview_expected_changes(
    mo2_root: Path | None,
    deps: Iterable[Dependency],
    *,
    pack_by_id: dict[str, Dependency] | None = None,
    enabled_names: Iterable[str] | None = None,
    disabled_names: Iterable[str] | None = None,
    feature: FeatureMeta | None = None,
) -> list[str]:
    """Live MO2 impact we cannot know from YAML alone (wizard Expected changes)."""
    by_id = pack_by_id or {}
    enabled = list(enabled_names or [])
    disabled = list(disabled_names or [])
    dep_list = list(deps)

    leaves: list[Dependency] = []
    seen_leaf: set[str] = set()
    for dep in dep_list:
        try:
            ids = expand_pack_composition(by_id, dep.id) if by_id else [dep.id]
        except ValueError:
            ids = [dep.id]
        for lid in ids:
            if lid in seen_leaf:
                continue
            seen_leaf.add(lid)
            leaf = by_id.get(lid)
            if leaf is not None:
                leaves.append(leaf)
            elif lid == dep.id:
                leaves.append(dep)

    lines: list[str] = []
    seen_line: set[str] = set()

    def _add(line: str) -> None:
        key = line.lower()
        if key in seen_line:
            return
        seen_line.add(key)
        lines.append(line)

    if mo2_root is not None:
        for leaf in leaves:
            if not (
                leaf.url
                or leaf.buy_url
                or leaf.path
                or leaf.source == "user"
            ):
                continue
            ok_present, _folders = dep_is_satisfied(mo2_root, leaf)
            if ok_present:
                continue
            if leaf.path:
                _add(f"ADD: DOGMA [{leaf.path.replace(chr(92), '/')}]")
            else:
                _add(f"ADD: {MANAGED_FOLDER_PREFIX}{leaf.id}")

    disable_patterns: list[str] = []
    enable_patterns: list[str] = []
    for leaf in leaves:
        disable_patterns.extend(leaf.disables)
        enable_patterns.extend(leaf.enables)
    if feature is not None:
        disable_patterns.extend(feature.disables)
        enable_patterns.extend(feature.enables)

    for name in mods_matching_patterns(enabled, disable_patterns):
        _add(f"DISABLE: {name}")
    for name in mods_matching_patterns(disabled, enable_patterns):
        _add(f"ENABLED: {name}")

    if mo2_root is not None:
        for leaf in leaves:
            if not leaf.has_axr_effects():
                continue
            ok_present, folders = dep_is_satisfied(mo2_root, leaf)
            if folders:
                for folder in folders:
                    _add(f"CONFIGURE: {folder}")
            elif leaf.path:
                _add("CONFIGURE: DOGMA")
            else:
                _add(f"CONFIGURE: {MANAGED_FOLDER_PREFIX}{leaf.id}")
        if feature is not None and feature.has_axr_effects():
            _add("CONFIGURE: DOGMA")

    console_cmds: list[str] = []
    for leaf in leaves:
        console_cmds.extend(leaf.console)
    if feature is not None:
        console_cmds.extend(feature.console)
    for cmd in _unique_strs(console_cmds):
        _add(f"RUN ONCE: {cmd}")

    return lines


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
        info(f"modlist: wrote {modlist} ({len(result.disabled)} newly disabled)")
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
    for name in enabled:
        ok(f"  enable: {name}" + (" (dry-run)" if dry_run else ""))
    return enabled


def read_initialize_ini(path: Path) -> list[InitSetting]:
    """Load defaults from manifest.yml (or legacy defaults.ini)."""
    if path.suffix.lower() in (".yml", ".yaml") or path.name in (
        "manifest.yml",
        "features.yml",
    ):
        return list(load_manifest(path).defaults)
    if not path.is_file():
        raise FileNotFoundError(f"defaults not found: {path}")
    out: list[InitSetting] = []
    section: str | None = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            if not section:
                raise ValueError("defaults.ini has an empty [section]")
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
        out.append(InitSetting(section, axr_section, key, val))
    return out


def find_present_mod(pattern: str, mod_names: Iterable[str]) -> str | None:
    """Return the first modlist name matching ``pattern`` (exact / substring: rules)."""
    rules = rules_from_disable_names([pattern])
    for name in mod_names:
        if mod_matches(name, rules):
            return name
    return None


_AXR_ASSIGN = re.compile(r"^(\s*)([^\s=]+)\s*=\s*(.*?)\s*$")


def format_axr_line(indent: str, key: str, value: str, width: int = 40) -> str:
    pad = max(width, len(key) + 1)
    return f"{indent}{key:<{pad}} = {value}"


_ID_DEF_LINE = re.compile(
    r"""(?ix)
    id\s*=\s*['"]([^'"]+)['"]
    .*?
    def\s*=\s*
    (
        -?\d+(?:\.\d+)?
        | true | false
        | ['"][^'"]*['"]
    )
    """
)
_ROOT_ID = re.compile(
    r"""(?ix)
    (?:function\s+on_mcm_load|return\s*\{)
    .*?
    id\s*=\s*['"]([^'"]+)['"]
    """,
    re.DOTALL,
)
_SIMPLE_ROOT = re.compile(
    r"""(?ix)
    ^\s*(?:op\s*=\s*)?\{\s*
    id\s*=\s*['"]([^'"]+)['"]
    """,
    re.MULTILINE,
)


def _normalize_mcm_def(raw: str) -> str:
    raw = raw.strip()
    if (raw.startswith("'") and raw.endswith("'")) or (
        raw.startswith('"') and raw.endswith('"')
    ):
        return raw[1:-1]
    if raw.lower() in ("true", "false"):
        return raw.lower()
    return raw


def index_mcm_script_defaults(mo2_root: Path) -> dict[str, str]:
    """Scan mods/**/*mcm*.script for id/def= pairs (and root/id paths)."""
    defaults: dict[str, str] = {}
    mods = mo2_root / "mods"
    if not mods.is_dir():
        return defaults
    for script in mods.rglob("*.script"):
        name = script.name.lower()
        if "mcm" not in name and not name.endswith("_mcm.script"):
            continue
        try:
            text = script.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        root = None
        m = _ROOT_ID.search(text)
        if m:
            root = m.group(1)
        else:
            m2 = _SIMPLE_ROOT.search(text)
            if m2:
                root = m2.group(1)
        for m in _ID_DEF_LINE.finditer(text):
            opt_id, def_raw = m.group(1), _normalize_mcm_def(m.group(2))
            defaults[opt_id] = def_raw
            if root and opt_id != root:
                defaults[f"{root}/{opt_id}"] = def_raw
    return defaults


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
    installed: set[str] | None = None,
    suggested_ids: set[str] | None = None,
) -> tuple[int, int]:
    """Apply MCM defaults into axr_options.ltx files.

    Keys are written blindly into axr_options once selected. Manual (no url:)
    packs are omitted unless that mod is installed.
    """
    if initialize_path.suffix.lower() in (".yml", ".yaml") or initialize_path.name in (
        "manifest.yml",
        "features.yml",
    ):
        to_apply = load_manifest(initialize_path).collect_defaults(
            installed=installed,
            mo2_root=mo2_root,
            modlist=modlist,
            suggested_ids=suggested_ids,
        )
    else:
        to_apply = read_initialize_ini(initialize_path)
    if not to_apply:
        return 0, 0

    files = 0
    values = 0
    for scan in (mo2_root / "mods", mo2_root / "overwrite"):
        for path in iter_files(scan, name="axr_options.ltx"):
            changes = apply_settings_to_axr_options(path, to_apply, dry_run)
            if changes:
                files += 1
                values += len(changes)
                try:
                    rel = path.relative_to(mo2_root)
                except ValueError:
                    rel = path
                info(f"defaults -> {rel} ({len(changes)} change(s))")
                for c in changes:
                    ok(f"  {c}")
    return files, values


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
    min_stage: str | int = "local",
    installed: set[str] | None = None,
) -> list[Dependency]:
    """tier: downloads (alias: required) | suggested | all.

    ``suggested`` / ``all`` on a ManifestData return wizard expanded packs
    only when used via deps_for_args; raw ``all`` here is feature downloads plus
    suggested packs deduped by id (prefer first occurrence).
    """
    t = tier.lower()
    if t == "required":
        t = "downloads"

    def _dedupe(items: list[Dependency]) -> list[Dependency]:
        out: list[Dependency] = []
        seen: set[str] = set()
        for d in items:
            if d.id in seen:
                continue
            seen.add(d.id)
            out.append(d)
        return out

    if isinstance(deps, ManifestData):
        if t == "downloads":
            return deps.feature_downloads(min_stage, installed=installed)
        if t == "suggested":
            return list(deps.suggested)
        if t == "all":
            return _dedupe(
                deps.feature_downloads(min_stage, installed=installed)
                + list(deps.suggested)
            )
        raise ValueError(f"unknown tier: {tier}")
    if t == "all":
        req = [d for d in deps if d.tier in ("downloads", "required")]
        sug = [d for d in deps if d.tier == "suggested"]
        return _dedupe(req + sug)
    if t == "downloads":
        return [d for d in deps if d.tier in ("downloads", "required")]
    if t == "suggested":
        return [d for d in deps if d.tier == "suggested"]
    raise ValueError(f"unknown tier: {tier}")


def grok_mods_txt(mo2_root: Path) -> Path:
    return mo2_root / ".Grok's Modpack Installer" / "mods.txt"


# ---------------------------------------------------------------------------
# ModDB page → start URL / filename / updated
# ---------------------------------------------------------------------------


@dataclass
class ModdbInfo:
    page_url: str
    start_url: str = ""
    file_id: str = ""
    filename: str = ""
    md5: str = ""
    size: str = ""
    added: str = ""
    updated: str = ""
    title: str = ""

    @property
    def version_hint(self) -> str:
        """Best-effort version string (often embedded in filename)."""
        name = self.filename or ""
        m = re.search(
            r"(?i)(?:^|[_\s-])v?(\d+(?:\.\d+){1,3})(?:[_\s-]|$)",
            Path(name).stem if name else "",
        )
        if m:
            return m.group(1)
        return self.updated or self.added or ""


def is_moddb_url(url: str) -> bool:
    return bool(url) and "moddb.com" in url.lower()


def is_discord_url(url: str) -> bool:
    """Discord channel/message/invite links — open in browser, manual archive."""
    u = (url or "").strip().lower()
    if not u:
        return False
    return any(
        host in u
        for host in (
            "discord.com/",
            "discord.gg/",
            "discordapp.com/",
            "discord.com?",
            "discord.gg?",
        )
    ) or u.rstrip("/").endswith("discord.com") or u.rstrip("/").endswith(
        "discord.gg"
    )


def is_kofi_url(url: str) -> bool:
    u = (url or "").strip().lower()
    return bool(u) and ("ko-fi.com" in u or "kofi.com" in u)


def is_patreon_url(url: str) -> bool:
    u = (url or "").strip().lower()
    return bool(u) and "patreon.com" in u


def is_auto_download_url(url: str) -> bool:
    """True when MO2 can fetch the archive (ModDB / GitHub)."""
    return is_moddb_url(url) or is_github_url(url)


def is_open_page_url(url: str) -> bool:
    """True when a typed url should open in a browser (not MO2 download)."""
    return (
        is_discord_url(url)
        or is_kofi_url(url)
        or is_patreon_url(url)
    )


def normalize_moddb_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    u = u.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    if u.lower().startswith("http://"):
        u = "https://" + u[7:]
    if "://moddb.com/" in u.lower():
        u = re.sub(r"(?i)://moddb\.com/", "://www.moddb.com/", u)
    m = re.search(r"(?i)/downloads/mirror/(\d+)/", u)
    if m:
        return f"https://www.moddb.com/downloads/start/{m.group(1)}"
    return u


def _moddb_file_id_from_url(url: str) -> str:
    u = normalize_moddb_url(url)
    m = re.search(r"(?i)/(?:addons|downloads)/start/(\d+)$", u)
    if m:
        return m.group(1)
    m = re.search(r"(?i)/downloads/mirror/(\d+)/", url or "")
    return m.group(1) if m else ""


def _moddb_summary_field(html: str, label: str) -> str:
    m = re.search(
        rf"(?is)<h5[^>]*>\s*{re.escape(label)}\s*</h5>\s*"
        rf'<span[^>]*class="summary"[^>]*>\s*(.*?)\s*</span>',
        html,
    )
    if not m:
        return ""
    inner = m.group(1)
    tm = re.search(r'(?is)<time[^>]*datetime="([^"]*)"[^>]*>([^<]*)', inner)
    if tm:
        return (tm.group(1) or tm.group(2) or "").strip()
    text = re.sub(r"(?is)<[^>]+>", " ", inner)
    return re.sub(r"\s+", " ", text).strip()


def _parse_moddb_html(page_url: str, html: str) -> ModdbInfo:
    info = ModdbInfo(page_url=normalize_moddb_url(page_url) or page_url)
    m = re.search(r"(?is)<title>\s*([^<]+?)\s*</title>", html)
    if m:
        title = m.group(1).strip()
        title = re.sub(r"\s+addon\s+-.*$", "", title, flags=re.I).strip()
        title = re.sub(r"\s+-\s+ModDB\s*$", "", title, flags=re.I).strip()
        info.title = title

    start = ""
    file_id = ""
    for m in re.finditer(
        r'href="((?:https://www\.moddb\.com)?/(?:addons|downloads)/start/(\d+))"',
        html,
        re.I,
    ):
        start = m.group(1)
        file_id = m.group(2)
        break
    if not file_id:
        m = re.search(r"(?i)siteareaid[=\"']+(\d+)", html)
        if m:
            file_id = m.group(1)
    if file_id:
        info.file_id = file_id
        if start.startswith("/"):
            start = "https://www.moddb.com" + start
        info.start_url = start or f"https://www.moddb.com/downloads/start/{file_id}"

    info.filename = _moddb_summary_field(html, "Filename")
    info.md5 = _moddb_summary_field(html, "MD5 Hash")
    info.size = _moddb_summary_field(html, "Size")
    info.added = _moddb_summary_field(html, "Added")
    info.updated = _moddb_summary_field(html, "Updated")
    return info


def _moddb_http_get(url: str) -> str:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "DOGMA-MO2/1.0 (+https://github.com/)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"ModDB HTTP {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"ModDB fetch failed for {url}: {exc.reason}") from exc


_MODDB_MIRROR_HREF_RE = re.compile(
    r'href="((?:https://www\.moddb\.com)?/downloads/mirror/\d+/[^"]+)"',
    re.I,
)
_MODDB_MIRROR_JS_RE = re.compile(
    r'window\.location\.href\s*=\s*"(https://www\.moddb\.com/downloads/mirror/[^"]+)"',
    re.I,
)


def resolve_moddb_mirror_url(start_url: str) -> str:
    """Resolve ModDB ``/start/`` interstitial HTML to a ``/downloads/mirror/...`` URL.

    Fetching a start page yields countdown HTML; the mirror link redirects to
    the real CDN zip that DOGMA downloads directly.
    """
    url = (start_url or "").strip()
    if not url:
        return ""
    if re.search(r"(?i)/downloads/mirror/\d+/", url):
        if url.startswith("/"):
            return "https://www.moddb.com" + url
        return url.split("#", 1)[0]
    # Addon/download start pages (and /start/<id>/all)
    if not re.search(r"(?i)/(?:addons|downloads)/start/\d+", url):
        return url
    html = _moddb_http_get(url)
    m = _MODDB_MIRROR_HREF_RE.search(html) or _MODDB_MIRROR_JS_RE.search(html)
    if not m:
        raise RuntimeError(
            f"ModDB start page has no mirror link (interstitial only): {url}"
        )
    mirror = m.group(1).strip()
    if mirror.startswith("/"):
        mirror = "https://www.moddb.com" + mirror
    return mirror.split("#", 1)[0]


def _looks_like_html_file(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            head = fh.read(96).lstrip().lower()
    except OSError:
        return False
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


def _purge_moddb_start_stubs(mo2_root: Path, *, file_id: str = "") -> None:
    """Remove bare ModDB start-page leftovers MO2 may have saved as ``<file_id>``."""
    fid = str(file_id or "").strip()
    if not fid.isdigit():
        return
    top = mo2_root / "downloads"
    if not top.is_dir():
        return
    for name in (fid, f"{fid}.meta"):
        path = top / name
        if not path.is_file():
            continue
        if name.endswith(".meta") or _looks_like_html_file(path) or not _is_archive_file(path):
            try:
                path.unlink()
                warn(f"  removed ModDB start-page stub: downloads/{name}")
            except OSError:
                pass


def _moddb_cache_path(cache_dir: Path | None) -> Path | None:
    if cache_dir is None:
        return None
    return cache_dir / MODDB_CACHE_NAME


def _moddb_cache_load(cache_dir: Path | None) -> dict:
    path = _moddb_cache_path(cache_dir)
    if not path or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _moddb_cache_save(cache_dir: Path | None, data: dict) -> None:
    path = _moddb_cache_path(cache_dir)
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def resolve_moddb(
    url: str,
    *,
    cache_dir: Path | None = None,
    force: bool = False,
) -> ModdbInfo:
    """Resolve a ModDB page / start / mirror URL to start link + file metadata."""
    raw = (url or "").strip()
    if not raw or not is_moddb_url(raw):
        raise ValueError(f"not a ModDB URL: {url!r}")

    norm = normalize_moddb_url(raw)
    cache = _moddb_cache_load(cache_dir)
    cached = cache.get(norm.lower()) if not force else None
    if isinstance(cached, dict) and cached.get("fetched_at"):
        try:
            age = datetime.now().timestamp() - float(cached["fetched_at"])
        except (TypeError, ValueError):
            age = MODDB_CACHE_MAX_AGE_S + 1
        if age <= MODDB_CACHE_MAX_AGE_S and cached.get("start_url"):
            return ModdbInfo(
                page_url=str(cached.get("page_url") or norm),
                start_url=str(cached.get("start_url") or ""),
                file_id=str(cached.get("file_id") or ""),
                filename=str(cached.get("filename") or ""),
                md5=str(cached.get("md5") or ""),
                size=str(cached.get("size") or ""),
                added=str(cached.get("added") or ""),
                updated=str(cached.get("updated") or ""),
                title=str(cached.get("title") or ""),
            )

    file_id = _moddb_file_id_from_url(norm)
    fetch_url = norm
    if file_id and re.search(r"(?i)/(?:addons|downloads)/start/\d+$", norm):
        fetch_url = f"https://www.moddb.com/downloads/{file_id}"

    html = _moddb_http_get(fetch_url)
    parsed = _parse_moddb_html(norm if "/mods/" in norm.lower() else fetch_url, html)
    if not parsed.start_url and file_id:
        parsed.file_id = file_id
        parsed.start_url = f"https://www.moddb.com/downloads/start/{file_id}"
    if not parsed.page_url:
        parsed.page_url = norm
    if "/mods/" in norm.lower() and "/addons/" in norm.lower():
        parsed.page_url = norm

    payload = {
        "page_url": parsed.page_url,
        "start_url": parsed.start_url,
        "file_id": parsed.file_id,
        "filename": parsed.filename,
        "md5": parsed.md5,
        "size": parsed.size,
        "added": parsed.added,
        "updated": parsed.updated,
        "title": parsed.title,
        "fetched_at": datetime.now().timestamp(),
    }
    cache[norm.lower()] = payload
    if parsed.page_url and parsed.page_url.lower() != norm.lower():
        cache[parsed.page_url.lower()] = payload
    _moddb_cache_save(cache_dir, cache)
    return parsed


# ---------------------------------------------------------------------------
# GitHub page → release asset / tag archive / source archive
# ---------------------------------------------------------------------------


@dataclass
class GithubInfo:
    page_url: str
    download_url: str = ""
    filename: str = ""
    tag: str = ""
    kind: str = ""  # release | tag | source
    updated: str = ""  # ISO or YYYY-MM-DD
    title: str = ""
    owner: str = ""
    repo: str = ""


def is_github_url(url: str) -> bool:
    u = (url or "").lower()
    return bool(u) and ("github.com/" in u or u.startswith("git@github.com:"))


def parse_github_repo(url: str) -> tuple[str, str]:
    """Return (owner, repo) from a github.com URL or git@github.com: SSH form."""
    u = (url or "").strip()
    if not u:
        raise ValueError("empty GitHub URL")
    m = re.search(
        r"(?i)(?:github\.com[:/]|git@github\.com:)(?P<owner>[^/\s]+)/(?P<repo>[^/\s?#]+)",
        u,
    )
    if not m:
        raise ValueError(f"not a GitHub repo URL: {url!r}")
    repo = m.group("repo")
    if repo.lower().endswith(".git"):
        repo = repo[:-4]
    return m.group("owner"), repo


def normalize_github_repo_url(url: str) -> str:
    owner, repo = parse_github_repo(url)
    return f"https://github.com/{owner}/{repo}"


def _github_http_json(url: str) -> dict | list:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "DOGMA-MO2/1.0 (+https://github.com/)",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {}
        raise RuntimeError(f"GitHub HTTP {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GitHub fetch failed for {url}: {exc.reason}") from exc


def _github_cache_path(cache_dir: Path | None) -> Path | None:
    if cache_dir is None:
        return None
    return cache_dir / GITHUB_CACHE_NAME


def _github_cache_load(cache_dir: Path | None) -> dict:
    path = _github_cache_path(cache_dir)
    if not path or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _github_cache_save(cache_dir: Path | None, data: dict) -> None:
    path = _github_cache_path(cache_dir)
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _git_ls_remote(repo_https: str, *args: str) -> list[tuple[str, str]]:
    """Return [(sha, ref), ...] from ``git ls-remote``."""
    cmd = ["git", "ls-remote", *args, repo_https]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"git ls-remote failed for {repo_https}: {exc}") from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"git ls-remote exit {proc.returncode}: {err or repo_https}")
    rows: list[tuple[str, str]] = []
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            rows.append((parts[0], parts[1]))
    return rows


def _github_tag_sort_key(tag: str) -> tuple:
    nums = [int(x) for x in re.findall(r"\d+", tag)]
    return (nums, tag.lower())


def _pick_github_release_asset(
    assets: list[dict],
    *,
    archive_name: str = "",
) -> dict | None:
    """Prefer archive assets; match archive_name when set; skip pdb dumps."""
    archives: list[dict] = []
    for raw in assets:
        name = str(raw.get("name") or "")
        if Path(name).suffix.lower() not in ARCHIVE_SUFFIXES:
            continue
        archives.append(raw)
    if not archives:
        return None

    def _score(asset: dict) -> tuple:
        name = str(asset.get("name") or "").lower()
        pdb = 1 if "pdb" in name else 0
        test = 1 if "mt-test" in name or "test" in name.split("_") else 0
        return (pdb, test, len(name), name)

    pool = archives
    needle = (archive_name or "").strip().lower()
    if needle:
        matched = [
            a
            for a in archives
            if needle in str(a.get("name") or "").lower()
            or needle.replace(" ", "") in re.sub(r"[^a-z0-9]+", "", str(a.get("name") or "").lower())
        ]
        if matched:
            pool = matched
    return sorted(pool, key=_score)[0]


def resolve_github(
    url: str,
    *,
    archive_name: str = "",
    cache_dir: Path | None = None,
    force: bool = False,
) -> GithubInfo:
    """Resolve a GitHub repo/releases URL to a downloadable archive.

    Order: latest release asset (API) → latest git tag archive → default-branch source.
    """
    raw = (url or "").strip()
    if not raw or not is_github_url(raw):
        raise ValueError(f"not a GitHub URL: {url!r}")

    owner, repo = parse_github_repo(raw)
    page = normalize_github_repo_url(raw)
    cache_key = f"{page}|{archive_name}".lower()
    cache = _github_cache_load(cache_dir)
    cached = cache.get(cache_key) if not force else None
    if isinstance(cached, dict) and cached.get("fetched_at") and cached.get("download_url"):
        try:
            age = datetime.now().timestamp() - float(cached["fetched_at"])
        except (TypeError, ValueError):
            age = GITHUB_CACHE_MAX_AGE_S + 1
        if age <= GITHUB_CACHE_MAX_AGE_S:
            return GithubInfo(
                page_url=str(cached.get("page_url") or page),
                download_url=str(cached.get("download_url") or ""),
                filename=str(cached.get("filename") or ""),
                tag=str(cached.get("tag") or ""),
                kind=str(cached.get("kind") or ""),
                updated=str(cached.get("updated") or ""),
                title=str(cached.get("title") or ""),
                owner=owner,
                repo=repo,
            )

    info = GithubInfo(page_url=page, owner=owner, repo=repo, title=f"{owner}/{repo}")
    api_latest = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    release = _github_http_json(api_latest)
    if isinstance(release, dict) and release.get("tag_name"):
        tag = str(release.get("tag_name") or "").strip()
        info.tag = tag
        info.updated = str(
            release.get("published_at") or release.get("created_at") or ""
        )
        assets = release.get("assets") if isinstance(release.get("assets"), list) else []
        asset = _pick_github_release_asset(
            [a for a in assets if isinstance(a, dict)],
            archive_name=archive_name,
        )
        if asset and asset.get("browser_download_url"):
            info.kind = "release"
            info.download_url = str(asset["browser_download_url"])
            info.filename = str(asset.get("name") or "")
        elif tag:
            info.kind = "release"
            info.download_url = (
                f"https://github.com/{owner}/{repo}/archive/refs/tags/{tag}.zip"
            )
            info.filename = f"{repo}-{tag}.zip"

    if not info.download_url:
        # Latest tag via git (no GitHub API needed).
        repo_git = f"https://github.com/{owner}/{repo}.git"
        try:
            rows = _git_ls_remote(repo_git, "--tags", "--refs")
        except RuntimeError:
            rows = []
        tags: list[str] = []
        for _sha, ref in rows:
            if ref.startswith("refs/tags/"):
                tags.append(ref[len("refs/tags/") :])
        if tags:
            tag = max(tags, key=_github_tag_sort_key)
            info.kind = "tag"
            info.tag = tag
            info.download_url = (
                f"https://github.com/{owner}/{repo}/archive/refs/tags/{tag}.zip"
            )
            info.filename = f"{repo}-{tag}.zip"

    if not info.download_url:
        # Default branch / HEAD source archive.
        branch = "HEAD"
        repo_git = f"https://github.com/{owner}/{repo}.git"
        try:
            rows = _git_ls_remote(repo_git, "--symref", "HEAD")
            for _sha, ref in rows:
                if ref.startswith("ref: refs/heads/"):
                    branch = ref[len("ref: refs/heads/") :]
                    break
        except RuntimeError:
            pass
        info.kind = "source"
        info.tag = branch
        if branch == "HEAD":
            info.download_url = f"https://github.com/{owner}/{repo}/archive/HEAD.zip"
            info.filename = f"{repo}-HEAD.zip"
        else:
            info.download_url = (
                f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"
            )
            info.filename = f"{repo}-{branch}.zip"

    if not info.download_url:
        raise RuntimeError(f"could not resolve GitHub download for {page}")

    cache[cache_key] = {
        "fetched_at": datetime.now().timestamp(),
        "page_url": info.page_url,
        "download_url": info.download_url,
        "filename": info.filename,
        "tag": info.tag,
        "kind": info.kind,
        "updated": info.updated,
        "title": info.title,
    }
    _github_cache_save(cache_dir, cache)
    return info


def github_date_stamp(github: GithubInfo | None) -> str:
    if not github:
        return ""
    return iso_to_date_stamp(github.updated) or iso_to_date_stamp(github.tag)


def dep_url_candidates(dep: Dependency, moddb: ModdbInfo | None = None) -> list[str]:
    """URLs to match against Grok mods.txt / meta.ini."""
    out: list[str] = []
    seen: set[str] = set()

    def add(u: str) -> None:
        n = normalize_moddb_url(u) if is_moddb_url(u) else (u or "").strip().rstrip("/")
        if not n:
            return
        key = n.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(n)

    add(dep.url)
    if moddb:
        add(moddb.page_url)
        add(moddb.start_url)
        if moddb.file_id:
            add(f"https://www.moddb.com/addons/start/{moddb.file_id}")
            add(f"https://www.moddb.com/downloads/start/{moddb.file_id}")
    elif dep.url and is_moddb_url(dep.url):
        fid = _moddb_file_id_from_url(dep.url)
        if fid:
            add(f"https://www.moddb.com/addons/start/{fid}")
            add(f"https://www.moddb.com/downloads/start/{fid}")
    elif dep.url and is_github_url(dep.url):
        try:
            add(normalize_github_repo_url(dep.url))
        except ValueError:
            pass
    return out


def catalog_rows_for_url(mo2_root: Path, url: str) -> list[tuple[int, str]]:
    """Return (1-based lineno, folder_name_guess) for mods.txt rows matching url."""
    return catalog_rows_for_urls(mo2_root, [url] if url else [])


def catalog_rows_for_urls(
    mo2_root: Path, urls: Iterable[str]
) -> list[tuple[int, str]]:
    path = grok_mods_txt(mo2_root)
    if not path.is_file():
        return []
    want: set[str] = set()
    for u in urls:
        if not u or not str(u).strip():
            continue
        n = normalize_moddb_url(u) if is_moddb_url(u) else str(u).strip().rstrip("/")
        if n:
            want.add(n.lower())
    if not want:
        return []

    def field_matches(field: str) -> bool:
        f = field.strip().lower().rstrip("/")
        if not f:
            return False
        if f in want:
            return True
        for w in want:
            if "moddb.com" in w and "moddb.com" in f:
                wp = w.split("moddb.com/", 1)[-1]
                fp = f.split("moddb.com/", 1)[-1]
                if wp and (wp == fp or f.endswith(wp) or w.endswith(fp)):
                    return True
        return False

    hits: list[tuple[int, str]] = []
    for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.strip() or line.startswith(" "):
            continue
        parts = line.split("\t")
        if not any(field_matches(p) for p in parts):
            continue
        display = parts[3].strip() if len(parts) > 3 else ""
        author = ""
        if len(parts) > 2:
            author = parts[2].replace(" - ", "").strip(" -")
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
    return find_catalog_folders_for_urls(mo2_root, [url] if url else [])


def find_catalog_folders_for_urls(mo2_root: Path, urls: Iterable[str]) -> list[str]:
    mods = mo2_root / "mods"
    found: list[str] = []
    for lineno, _guess in catalog_rows_for_urls(mo2_root, urls):
        prefix = f"{lineno}-"
        for d in mods.iterdir() if mods.is_dir() else []:
            if d.is_dir() and d.name.startswith(prefix) and d.name not in found:
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
    moddb: ModdbInfo | None = None,
) -> tuple[bool, list[str]]:
    if dep.path:
        path = dep.path.replace("\\", "/")
        hit = path in detect_installed_features(mo2_root, [path])
        folders = ["DOGMA"] if hit else []
        if not hit:
            return False, []
        if not require_enabled or modlist is None:
            return True, folders
        enabled = {n for f, n in list_modlist_entries(modlist) if f == "+"}
        return ("DOGMA" in enabled or any("DOGMA" in n.upper() for n in enabled), folders)

    folders: list[str] = []
    # Manual (no url:): only our managed install counts — never catalog / lookalikes.
    # Effects (disables / enables / mcm / settings / console) stay off until it exists.
    if dep.source == "user" or not dep.url:
        for name in find_managed_folders(mo2_root, dep):
            if name not in folders:
                folders.append(name)
    else:
        urls = dep_url_candidates(dep, moddb)
        if urls:
            folders.extend(find_catalog_folders_for_urls(mo2_root, urls))
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


def dep_effects_active(
    mo2_root: Path,
    dep: Dependency,
    modlist: Path,
) -> bool:
    """True when this pack's disables/enables/mcm/settings/console should run."""
    satisfied, _ = dep_is_satisfied(
        mo2_root, dep, modlist, require_enabled=True
    )
    return satisfied


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
    ok(f"  modlist enable under separator: {mod_name}" + (" (dry-run)" if dry_run else ""))


def wipe_managed_mod(mo2_root: Path, modlist: Path, folder_name: str, dry_run: bool) -> None:
    # Never wipe Grok-numbered catalog folders
    if re.match(r"^\d+-", folder_name):
        warn(f"  Refusing to wipe catalog folder: {folder_name}")
        return
    lines = [l for l in read_text_lines(modlist) if not re.match(rf"^[+\-]{re.escape(folder_name)}$", l)]
    target = mo2_root / "mods" / folder_name
    info(
        f"  wipe managed {'(dry-run) ' if dry_run else ''}"
        f"{folder_name} (modlist entry + {target})"
    )
    if not dry_run:
        stamp_backup(modlist)
        write_text_lines(modlist, lines)
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)


ARCHIVE_SUFFIXES = {".zip", ".7z", ".rar", ".7zip"}
# Version compare when the stem ends in a date: "… 2025-02-27" / "…-2025-02-27" / "…_2025-02-27".
_ARCHIVE_DATE_RE = re.compile(r"^(?P<head>.+?)(?:[ _-])(?P<date>\d{4}-\d{2}-\d{2})$")


def archive_filename_date(path: Path | str) -> str:
    """YYYY-MM-DD if the archive stem ends with a date; else \"\"."""
    stem = Path(path).stem
    m = _ARCHIVE_DATE_RE.match(stem)
    return m.group("date") if m else ""


def archive_filename_head(path: Path | str) -> str:
    """Stem with trailing date suffix removed (or full stem if undated)."""
    stem = Path(path).stem
    m = _ARCHIVE_DATE_RE.match(stem)
    return (m.group("head").strip() if m else stem).strip()


def game_dir(mo2_root: Path) -> Path:
    """Game directory from ModOrganizer.ini gamePath (e.g. C:\\Anomaly)."""
    raw = read_mo2_ini_value(mo2_root / "ModOrganizer.ini", "gamePath")
    return Path(raw)


def expand_path_placeholders(text: str, mo2_root: Path | None) -> str:
    """Replace ``<Anomaly>`` / ``<GAMMA>`` with resolved paths when known."""
    if not text or mo2_root is None:
        return text
    out = text
    gamma = str(Path(mo2_root).resolve()).replace("/", "\\")
    out = out.replace("<GAMMA>", gamma).replace("<gamma>", gamma)
    try:
        game = str(game_dir(mo2_root)).replace("/", "\\")
        if game.strip():
            out = out.replace("<Anomaly>", game).replace("<anomaly>", game)
    except (OSError, FileNotFoundError, ValueError):
        pass
    return out


def format_preview_path(mo2_root: Path | None, raw: str) -> str:
    """Display path for wizard/FOMOD previews (resolved when MO2 root is known)."""
    s = (raw or "").strip()
    if not s:
        return s
    if mo2_root is not None:
        try:
            resolved = resolve_managed_path(mo2_root, s)
            if resolved is not None:
                return str(resolved).replace("/", "\\")
        except (OSError, FileNotFoundError, ValueError):
            pass
    return expand_path_placeholders(s, mo2_root)


# deletes: path roots — <Anomaly> = gamePath, <GAMMA> = MO2 instance
_PATH_ROOT_RE = re.compile(
    r"^(?:<(anomaly|gamma)>|(anomaly|gamma|game|mo2):)[/\\]?(.*)$",
    re.IGNORECASE,
)


def resolve_managed_path(mo2_root: Path, raw: str) -> Path | None:
    """Resolve a path that may be rooted at Anomaly (game) or GAMMA (MO2).

    - ``<Anomaly>/rel`` / ``anomaly:rel`` / ``game:rel`` → ModOrganizer gamePath
    - ``<GAMMA>/rel`` / ``gamma:rel`` / ``mo2:rel`` → MO2 instance root
    - Absolute path → as-is
    - Bare relative path → under Anomaly (game) by default
    """
    s = (raw or "").strip().replace("\\", "/")
    if not s or s.startswith("#"):
        return None
    m = _PATH_ROOT_RE.match(s)
    if m:
        root_name = (m.group(1) or m.group(2) or "").lower()
        rel = (m.group(3) or "").lstrip("/\\")
        if root_name in ("anomaly", "game"):
            base = game_dir(mo2_root)
        else:  # gamma | mo2
            base = mo2_root
        return (base / rel).resolve() if rel else base.resolve()
    p = Path(s)
    if p.is_absolute():
        return p.resolve()
    return (game_dir(mo2_root) / s).resolve()


# Compat alias
resolve_delete_path = resolve_managed_path


def _path_under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def run_dep_deletes(mo2_root: Path, dep: Dependency, *, dry_run: bool) -> list[str]:
    """Delete files/dirs listed on the dep (after install). Returns deleted paths."""
    if not dep.deletes:
        return []
    game = game_dir(mo2_root)
    deleted: list[str] = []
    for raw in dep.deletes:
        target = resolve_managed_path(mo2_root, raw)
        if target is None:
            continue
        if not _path_under(target, game) and not _path_under(target, mo2_root):
            warn(f"  [{dep.id}] deletes skipped (outside <Anomaly>/<GAMMA>): {raw}")
            continue
        if not target.exists():
            info(f"  [{dep.id}] deletes miss (already gone): {target}")
            continue
        info(f"  [{dep.id}] deletes {'(dry-run) ' if dry_run else ''}{target}")
        if dry_run:
            deleted.append(str(target))
            continue
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            deleted.append(str(target))
        except OSError as exc:
            warn(f"  [{dep.id}] deletes failed {target}: {exc}")
    return deleted


def run_dep_moves(
    mo2_root: Path,
    dep: Dependency,
    mod_dir: Path,
    *,
    dry_run: bool,
) -> list[str]:
    """Copy files from the unpacked managed mod to <Anomaly>/<GAMMA> destinations.

    Uses copy (not move) so Install/Update can re-run safely every time.
    """
    if not dep.moves:
        return []
    game = game_dir(mo2_root)
    moved: list[str] = []
    for src_rel, dest_raw in dep.moves:
        src = (mod_dir / src_rel.replace("\\", "/")).resolve()
        dest = resolve_managed_path(mo2_root, dest_raw)
        if dest is None:
            continue
        if not _path_under(dest, game) and not _path_under(dest, mo2_root):
            warn(f"  [{dep.id}] moves skipped (outside <Anomaly>/<GAMMA>): {dest_raw}")
            continue
        if not src.exists():
            warn(f"  [{dep.id}] moves miss src: {src_rel} (under {mod_dir.name})")
            continue
        info(
            f"  [{dep.id}] moves {'(dry-run) ' if dry_run else ''}"
            f"{src_rel} -> {dest}"
        )
        if dry_run:
            moved.append(f"{src} -> {dest}")
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                if dest.is_dir():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()
            if src.is_dir():
                shutil.copytree(src, dest)
            else:
                shutil.copy2(src, dest)
            moved.append(f"{src} -> {dest}")
        except OSError as exc:
            warn(f"  [{dep.id}] moves failed {src_rel}: {exc}")
    return moved


def dogma_once_root(mo2_root: Path) -> Path:
    return game_dir(mo2_root) / "appdata" / "dogma_once"


def console_pending_dir(mo2_root: Path) -> Path:
    return dogma_once_root(mo2_root) / "pending"


def console_done_dir(mo2_root: Path) -> Path:
    return dogma_once_root(mo2_root) / "done"


def console_once_done(mo2_root: Path, dep_id: str) -> bool:
    return (console_done_dir(mo2_root) / dep_id).is_file()


def queue_console_cmds(mo2_root: Path, dep: Dependency, *, dry_run: bool) -> bool:
    """Queue console cmds for next game boot (every installer run; clears done stamp)."""
    if not dep.console:
        return False
    pending = console_pending_dir(mo2_root)
    index = pending / "_index.txt"
    pack_file = pending / f"{dep.id}.txt"
    done = console_done_dir(mo2_root) / dep.id
    info(
        f"  [{dep.id}] console {'(dry-run) ' if dry_run else ''}"
        f"{len(dep.console)} cmd(s) -> {pack_file}"
    )
    for cmd in dep.console:
        info(f"    console: {cmd}")
    if dry_run:
        return True
    pending.mkdir(parents=True, exist_ok=True)
    console_done_dir(mo2_root).mkdir(parents=True, exist_ok=True)
    if done.is_file():
        done.unlink()
    pack_file.write_text(
        "\n".join(dep.console) + "\n",
        encoding="utf-8",
    )
    existing: list[str] = []
    if index.is_file():
        existing = [
            ln.strip()
            for ln in index.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
    if dep.id not in existing:
        existing.append(dep.id)
    index.write_text("\n".join(existing) + "\n", encoding="utf-8")
    return True


# Compat alias
queue_console_once = queue_console_cmds


def run_dep_side_effects(
    mo2_root: Path,
    dep: Dependency,
    mod_dir: Path | None,
    *,
    dry_run: bool,
) -> None:
    """Re-apply moves/deletes/console every installer run (idempotent)."""
    if mod_dir is not None and dep.moves:
        run_dep_moves(mo2_root, dep, mod_dir, dry_run=dry_run)
    run_dep_deletes(mo2_root, dep, dry_run=dry_run)
    queue_console_cmds(mo2_root, dep, dry_run=dry_run)


def _maybe_restore_dogma_downloads_sidecars(mo2_root: Path) -> None:
    """If archives were briefly moved to ``DOGMA-downloads/``, put them back."""
    root = Path(mo2_root)
    key = str(root.resolve()).lower()
    with _legacy_migrate_lock:
        if key in _legacy_migrated_roots:
            return
        _legacy_migrated_roots.add(key)
        side = root / "DOGMA-downloads"
        if not side.is_dir():
            return
        dest = root / "downloads" / "DOGMA"
        dest.mkdir(parents=True, exist_ok=True)
        moved = 0
        for p in side.iterdir():
            if not p.is_file():
                continue
            low = p.name.lower()
            if low.endswith(".partial") or low.endswith(".downloading"):
                continue
            target = dest / p.name
            if target.exists():
                continue
            try:
                _move_download_file(p, target)
                moved += 1
            except OSError as exc:
                warn(f"could not restore {p.name} -> downloads/DOGMA: {exc}")
        if moved:
            ok(f"restored {moved} file(s) DOGMA-downloads -> downloads/DOGMA")


def downloads_dir(mo2_root: Path) -> Path:
    """DOGMA archive store: ``<MO2>/downloads/DOGMA`` (direct HTTP, not MO2 CLI)."""
    d = Path(mo2_root) / "downloads" / "DOGMA"
    d.mkdir(parents=True, exist_ok=True)
    _maybe_restore_dogma_downloads_sidecars(mo2_root)
    return d


ARCHIVES_INI_NAME = "archives.ini"
ARCHIVES_INI_SECTION = "archives"


def archives_ini_path(mo2_root: Path) -> Path:
    return downloads_dir(mo2_root) / ARCHIVES_INI_NAME


def load_archive_map(mo2_root: Path) -> dict[str, str]:
    """pack id → basename under downloads/DOGMA/ (from archives.ini)."""
    path = archives_ini_path(mo2_root)
    if not path.is_file():
        return {}
    cfg = configparser.ConfigParser()
    try:
        raw = path.read_text(encoding="utf-8")
        # Allow keys with spaces / punctuation (pack ids).
        cfg.optionxform = str  # type: ignore[method-assign]
        cfg.read_string(raw)
    except (OSError, configparser.Error, UnicodeError):
        warn(f"could not read archive map: {path}")
        return {}
    if not cfg.has_section(ARCHIVES_INI_SECTION):
        return {}
    out: dict[str, str] = {}
    for key, val in cfg.items(ARCHIVES_INI_SECTION):
        kid = str(key).strip()
        name = str(val).strip()
        if kid and name:
            out[kid] = Path(name).name
    return out


def save_archive_map(mo2_root: Path, mapping: dict[str, str]) -> Path:
    """Write pack id → basename map to downloads/DOGMA/archives.ini."""
    path = archives_ini_path(mo2_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg = configparser.ConfigParser()
    cfg.optionxform = str  # type: ignore[method-assign]
    cfg.add_section(ARCHIVES_INI_SECTION)
    for kid in sorted(mapping.keys(), key=lambda s: s.lower()):
        name = Path(str(mapping[kid]).strip()).name
        if not kid.strip() or not name:
            continue
        cfg.set(ARCHIVES_INI_SECTION, kid.strip(), name)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        cfg.write(fh)
    return path


def set_archive_map_entry(mo2_root: Path, dep_id: str, basename: str) -> dict[str, str]:
    """Upsert one pack → basename entry and save."""
    with _archive_map_lock:
        mapping = load_archive_map(mo2_root)
        kid = str(dep_id).strip()
        name = Path(str(basename).strip()).name
        if not kid or not name:
            return mapping
        mapping[kid] = name
        path = save_archive_map(mo2_root, mapping)
    info(f"  archives.ini: [{kid}]={name} ({path})")
    return mapping


def clear_archive_map_entry(mo2_root: Path, dep_id: str) -> dict[str, str]:
    with _archive_map_lock:
        mapping = load_archive_map(mo2_root)
        kid = str(dep_id).strip()
        if kid in mapping:
            del mapping[kid]
            save_archive_map(mo2_root, mapping)
        return mapping


def mapped_archive_path(mo2_root: Path, dep: Dependency | str) -> Path | None:
    """Resolved downloads/DOGMA file from archives.ini, if present on disk."""
    dep_id = dep.id if isinstance(dep, Dependency) else str(dep)
    name = load_archive_map(mo2_root).get(str(dep_id).strip(), "").strip()
    if not name:
        return None
    path = downloads_dir(mo2_root) / Path(name).name
    if _is_archive_file(path):
        return path
    return None


def prune_missing_archive_map(mo2_root: Path) -> list[str]:
    """Drop archives.ini entries whose files are missing; return removed pack ids."""
    with _archive_map_lock:
        mapping = load_archive_map(mo2_root)
        if not mapping:
            return []
        dld = downloads_dir(mo2_root)
        keep: dict[str, str] = {}
        removed: list[str] = []
        for kid, name in mapping.items():
            path = dld / Path(name).name
            if _is_archive_file(path):
                keep[kid] = Path(name).name
            else:
                removed.append(kid)
        if removed:
            save_archive_map(mo2_root, keep)
    for kid in removed:
        warn(f"archives.ini: removed missing link [{kid}]")
    return removed


def sync_archive_links(mo2_root: Path) -> list[str]:
    """Reconcile archives.ini with downloads/DOGMA on disk.

    Drops map entries whose files are gone. Canonical ``<id>[ date].ext``
    files remain discoverable via ``list_dep_archives`` / filename scan.
    Returns pack ids removed from the map.
    """
    return prune_missing_archive_map(mo2_root)


def pack_needs_archive(dep: Dependency) -> bool:
    """True when Setup should show an archive field (not a path: mod)."""
    if dep.path:
        return False
    if is_wizard_radio_parent(dep):
        return False
    # Pure composition (requires only) — leaves get the fields, not this node.
    if dep.requires and not dep.has_remote_links() and not dep.enables:
        return False
    return bool(dep.has_remote_links() or dep.source == "user")


def dep_auto_download_url(dep: Dependency) -> str:
    """URL used for DOGMA auto-download (url_download: / legacy url:)."""
    for candidate in (dep.url_download, dep.url):
        u = (candidate or "").strip()
        if u and is_auto_download_url(u):
            return u
    return ""


def dep_wizard_link_actions(dep: Dependency) -> list[tuple[str, str, str]]:
    """Wizard file-box link buttons: (kind, url, mode) mode=download|open.

    kind is download|moddb|github|discord|kofi|patreon.
    """
    out: list[tuple[str, str, str]] = []
    dl = dep_auto_download_url(dep)
    if dl:
        out.append(("download", dl, "download"))
    if dep.url_moddb:
        out.append(("moddb", dep.url_moddb.strip(), "open"))
    if dep.url_github:
        out.append(("github", dep.url_github.strip(), "open"))
    if dep.url_discord:
        out.append(("discord", dep.url_discord.strip(), "open"))
    if dep.url_kofi:
        out.append(("kofi", dep.url_kofi.strip(), "open"))
    elif dep.buy_url and is_kofi_url(dep.buy_url):
        out.append(("kofi", dep.buy_url.strip(), "open"))
    if dep.url_patreon:
        out.append(("patreon", dep.url_patreon.strip(), "open"))
    elif dep.buy_url and is_patreon_url(dep.buy_url):
        out.append(("patreon", dep.buy_url.strip(), "open"))
    return out


def archive_leaves_for_pack(
    pack_by_id: dict[str, Dependency],
    pack_id: str,
    *,
    _stack: set[str] | None = None,
) -> list[Dependency]:
    """Packs that need an archive field when installing this pack/choice.

    Walks ``requires:`` so multi-zip bundles (e.g. AlifePlus + xlibs + …) each
    get a field. Pure composition nodes (no url) only contribute their requires.
    """
    stack = _stack if _stack is not None else set()
    pid = str(pack_id).strip()
    if not pid or pid in stack:
        return []
    pack = pack_by_id.get(pid)
    if pack is None:
        return []

    stack.add(pid)
    out: list[Dependency] = []
    seen: set[str] = set()
    try:
        # Composition-only radio choice: fields come from requires only.
        composition = (
            bool(pack.requires)
            and not pack.has_remote_links()
            and not pack.path
            and not is_wizard_radio_parent(pack)
        )
        if not composition and pack_needs_archive(pack):
            out.append(pack)
            seen.add(pack.id)

        for dep_id in pack.requires:
            for leaf in archive_leaves_for_pack(
                pack_by_id, dep_id, _stack=stack
            ):
                if leaf.id in seen:
                    continue
                seen.add(leaf.id)
                out.append(leaf)
    finally:
        stack.discard(pid)
    return out


def _unique_download_dest(dld: Path, filename: str) -> Path:
    """Prefer original basename; on collision append -2, -3, … before suffix."""
    name = Path(filename).name
    dest = dld / name
    if not dest.exists():
        return dest
    stem = Path(name).stem
    suffix = Path(name).suffix
    n = 2
    while True:
        candidate = dld / f"{stem}-{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1
        if n > 9999:
            raise RuntimeError(f"too many name collisions for {name} in {dld}")


def associate_archive(
    mo2_root: Path,
    dep_id: str,
    src_path: Path | str,
    *,
    date: str = "",
    dry_run: bool = False,
) -> Path:
    """Copy/rename an archive into downloads/DOGMA as ``<id>[ date].ext``."""
    src = Path(src_path)
    info(f"Associate archive: [{dep_id}] <- {src}")
    if not _is_archive_file(src):
        raise ValueError(
            f"not an archive ({', '.join(sorted(ARCHIVE_SUFFIXES))}): {src}"
        )
    dld = downloads_dir(mo2_root)
    dld.mkdir(parents=True, exist_ok=True)
    src_resolved = src.resolve()
    dld_resolved = dld.resolve()

    stamp = iso_to_date_stamp(date)
    if not stamp:
        stamp = archive_filename_date(src)
    suffix = src.suffix.lower() if src.suffix else _archive_magic_suffix(src)
    if not suffix:
        suffix = ".zip"
    dest = dld / f"{dep_zip_stem(dep_id, date=stamp)}{suffix}"

    if src_resolved == dest.resolve():
        info(f"  already canonical: {dest.name}")
    elif dry_run:
        info(f"Would store archive as {dest.name}")
    else:
        if dest.exists() and dest.resolve() != src_resolved:
            dest.unlink()
        if src_resolved.parent == dld_resolved:
            _move_download_file(src_resolved, dest)
            ok(f"  renamed archive -> {dest.name}")
        else:
            shutil.copy2(src_resolved, dest)
            ok(f"  copied archive -> {dest.name}")
    if not dry_run:
        set_archive_map_entry(mo2_root, dep_id, dest.name)
        ok(f"  linked [{dep_id}] -> {dest.name}")
    return dest


def download_and_associate(
    mo2_root: Path,
    dep: Dependency,
    *,
    dry_run: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Download (ModDB/GitHub) into downloads/DOGMA and write archives.ini entry.

    ``on_progress(bytes_done, bytes_total)`` — ``bytes_total`` may be 0 when unknown.
    """
    download_url = dep_auto_download_url(dep)
    if not download_url:
        raise ValueError(f"[{dep.id}] has no auto-download url_download:/url:")
    tools = mo2_tools_dir(mo2_root)
    info(f"Wizard download: [{dep.id}] {download_url}")
    moddb: ModdbInfo | None = None
    github: GithubInfo | None = None
    if is_moddb_url(download_url):
        info(f"  [{dep.id}] resolving ModDB…")
        moddb = resolve_moddb(download_url, cache_dir=tools)
        download_url = moddb.start_url or download_url
        bits = []
        if moddb.filename:
            bits.append(moddb.filename)
        stamp = moddb_date_stamp(moddb)
        if stamp:
            bits.append(f"date {stamp}")
        if bits:
            info(f"  [{dep.id}] ModDB: {', '.join(bits)}")
        info(f"  [{dep.id}] start URL: {download_url}")
        try:
            mirror = resolve_moddb_mirror_url(download_url)
            if mirror and mirror.rstrip("/") != download_url.rstrip("/"):
                info(f"  [{dep.id}] ModDB mirror: {mirror}")
                download_url = mirror
        except RuntimeError as exc:
            warn(f"  [{dep.id}] ModDB mirror resolve failed ({exc})")
    elif is_github_url(download_url):
        info(f"  [{dep.id}] resolving GitHub…")
        github = resolve_github(
            download_url,
            archive_name=dep.archive_name,
            cache_dir=tools,
        )
        download_url = github.download_url or download_url
        bits = [github.kind or "github"]
        if github.tag:
            bits.append(github.tag)
        if github.filename:
            bits.append(github.filename)
        info(f"  [{dep.id}] GitHub: {', '.join(bits)}")
        info(f"  [{dep.id}] download URL: {download_url}")
    else:
        raise ValueError(f"[{dep.id}] url: must be ModDB or GitHub")
    archive, note = ensure_dep_archive(
        mo2_root,
        dep,
        download_url=download_url,
        moddb=moddb,
        github=github,
        dry_run=dry_run,
        on_progress=on_progress,
    )
    if archive is None or not archive.is_file():
        if note == "missing-moddb-interstitial":
            raise RuntimeError(
                f"[{dep.id}] ModDB returned a countdown page instead of the zip — "
                "mirror resolve failed; try again or link the archive with 📁"
            )
        raise RuntimeError(f"[{dep.id}] download failed ({note})")
    if not dry_run:
        set_archive_map_entry(mo2_root, dep.id, archive.name)
        ok(f"  [{dep.id}] associated -> {archive.name} ({note})")
    return archive


def iso_to_date_stamp(raw: str) -> str:
    """Trim HTML datetime / ISO value to YYYY-MM-DD (empty if unparseable)."""
    s = (raw or "").strip()
    if not s:
        return ""
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", s)
    return m.group(1) if m else ""


def moddb_date_stamp(moddb: ModdbInfo | None) -> str:
    """Prefer Updated, else Added — both usually from <time datetime=\"…\">."""
    if not moddb:
        return ""
    return iso_to_date_stamp(moddb.updated) or iso_to_date_stamp(moddb.added)


def _safe_archive_stem(name: str) -> str:
    """Filesystem-safe stem; keeps spaces (catalog ids) but strips reserved chars."""
    bad = '<>:"/\\|?*'
    out = "".join("_" if c in bad else c for c in (name or "").strip())
    out = out.strip(" .")
    return out or "mod"


def dep_zip_stem(dep: Dependency | str, *, date: str = "") -> str:
    """Local archive basename: catalog ``<id>`` or ``<id> YYYY-MM-DD``.

    Always uses the internal mod id (not ``archive_name:``, which is only for
    matching upstream zip titles when claiming a download).
    """
    if isinstance(dep, Dependency):
        base = _safe_archive_stem(dep.id)
    else:
        base = _safe_archive_stem(str(dep))
    stamp = iso_to_date_stamp(date)
    return f"{base} {stamp}" if stamp else base


def preview_install_packs(
    deps: Iterable[Dependency],
    pack_by_id: dict[str, Dependency] | None = None,
) -> list[str]:
    """Pack ids that download/install (url, buy_url / user archive, or enables)."""
    out: list[str] = []
    seen: set[str] = set()
    by_id = pack_by_id or {}
    for dep in deps:
        try:
            leaves = expand_pack_composition(by_id, dep.id) if by_id else [dep.id]
        except ValueError:
            leaves = [dep.id]
        for lid in leaves:
            leaf = by_id.get(lid, dep if lid == dep.id else None)
            if leaf is None or lid in seen:
                continue
            if leaf.url or leaf.buy_url or leaf.source == "user" or leaf.enables:
                seen.add(lid)
                out.append(lid)
    return out


def preview_effect_sections(
    deps: Iterable[Dependency],
    *,
    mo2_root: Path | None = None,
) -> list[tuple[str, list[str]]]:
    """Ordered (label, values) for wizard/FOMOD catalog effect lists."""
    dep_list = list(deps)
    disables: list[str] = []
    enables: list[str] = []
    deletes: list[str] = []
    resets: list[str] = []
    moves: list[str] = []
    mcm: list[str] = []
    settings: list[str] = []
    for d in dep_list:
        disables.extend(d.disables)
        enables.extend(d.enables)
        deletes.extend(format_preview_path(mo2_root, p) for p in d.deletes)
        resets.extend(d.resets)
        moves.extend(
            f"{src} → {format_preview_path(mo2_root, dst)}" for src, dst in d.moves
        )
        mcm.extend(f"{k}={v}" for k, v in d.mcm.items())
        settings.extend(f"{k}={v}" for k, v in d.settings.items())

    def uniq(items: list[str]) -> list[str]:
        return _unique_strs(items)

    sections: list[tuple[str, list[str]]] = []
    for label, items in (
        ("Disables", uniq(disables)),
        ("Enables", uniq(enables)),
        ("Deletes", uniq(deletes)),
        ("Resets MCM", uniq(resets)),
        ("MCM", uniq(mcm)),
        ("Settings", uniq(settings)),
        ("Moves", uniq(moves)),
    ):
        if items:
            sections.append((label, items))
    return sections


def _archive_ext_rank(path: Path) -> tuple[int, str]:
    rank = {".zip": 0, ".7z": 1, ".7zip": 2, ".rar": 3}
    return (rank.get(path.suffix.lower(), 9), path.name.lower())


def _prefer_archive(paths: list[Path]) -> Path | None:
    if not paths:
        return None
    return sorted(paths, key=_archive_ext_rank)[0]


@dataclass(frozen=True)
class LocalArchive:
    path: Path
    pinned: bool  # undated <id>.* — never auto-replaced
    date: str  # YYYY-MM-DD when dated; "" if pinned


def list_dep_archives(mo2_root: Path, dep: Dependency) -> list[LocalArchive]:
    """Archives in downloads/DOGMA for this catalog id (map + stem matches)."""
    dld = downloads_dir(mo2_root)
    base = (dep.id or "").strip()
    if not base or not dld.is_dir():
        return []
    out: list[LocalArchive] = []
    seen: set[str] = set()

    def _add(path: Path, *, pinned: bool, date: str) -> None:
        key = str(path.resolve()).lower()
        if key in seen:
            return
        if not _is_archive_file(path):
            return
        seen.add(key)
        out.append(LocalArchive(path, pinned=pinned, date=date))

    # archives.ini may point at any basename; date suffix enables version compare.
    mapped = mapped_archive_path(mo2_root, dep)
    if mapped is not None:
        date = archive_filename_date(mapped)
        _add(mapped, pinned=not bool(date), date=date)

    stems = {base.lower()}
    an = (dep.archive_name or "").strip()
    if an:
        stems.add(an.lower())

    for p in dld.iterdir():
        if not _is_archive_file(p):
            continue
        stem = p.stem
        date = archive_filename_date(p)
        head = archive_filename_head(p).lower()
        if stem.lower() in stems:
            _add(p, pinned=True, date="")
            continue
        if date and head in stems:
            _add(p, pinned=False, date=date)
    return out


def resolve_local_archive(
    mo2_root: Path,
    dep: Dependency,
    *,
    remote_date: str = "",
) -> tuple[Path | None, str]:
    """Pick a local archive and report status vs ModDB/GitHub date.

    Status:
      pinned  — undated archive; never auto-replaced
      current — dated archive matches remote_date (or dated with no remote)
      stale   — have dated archive(s) but none match remote (remote newer)
      missing — nothing on disk
    """
    locals_ = list_dep_archives(mo2_root, dep)
    want = iso_to_date_stamp(remote_date)

    # Undated / user-pinned copies win only when we cannot date-compare.
    pinned = [a for a in locals_ if a.pinned]
    dated = [a for a in locals_ if a.date]
    if want:
        match = [a for a in dated if a.date == want]
        if match:
            return _prefer_archive([a.path for a in match]), "current"
        if dated:
            dated.sort(key=lambda a: a.date, reverse=True)
            return (
                _prefer_archive([a.path for a in dated if a.date == dated[0].date]),
                "stale",
            )
        if pinned:
            return _prefer_archive([a.path for a in pinned]), "pinned"
        return None, "missing"

    if pinned:
        return _prefer_archive([a.path for a in pinned]), "pinned"
    if dated:
        dated.sort(key=lambda a: a.date, reverse=True)
        return _prefer_archive([a.path for a in dated if a.date == dated[0].date]), "current"
    return None, "missing"


def probe_dep_remote_date(
    dep: Dependency,
    *,
    cache_dir: Path | None = None,
) -> str:
    """Best-effort ModDB/GitHub release date (YYYY-MM-DD); uses on-disk caches."""
    download_url = dep_auto_download_url(dep)
    if not download_url:
        return ""
    try:
        if is_moddb_url(download_url):
            return moddb_date_stamp(resolve_moddb(download_url, cache_dir=cache_dir))
        if is_github_url(download_url):
            return github_date_stamp(
                resolve_github(
                    download_url,
                    archive_name=dep.archive_name,
                    cache_dir=cache_dir,
                )
            )
    except Exception:
        return ""
    return ""


def local_archive_is_outdated(
    mo2_root: Path,
    dep: Dependency,
    *,
    remote_date: str,
) -> bool:
    """True when a dated local archive exists and remote_date is newer."""
    _path, status = resolve_local_archive(
        mo2_root, dep, remote_date=remote_date
    )
    return status == "stale"


def download_version_icon_status(
    mo2_root: Path,
    dep: Dependency,
    *,
    remote_date: str = "",
    probed: bool = False,
) -> str:
    """Wizard download-icon state: ``new`` | ``current`` | ``unknown``.

    Caller maps no-auto-download URL to grey (``none``) separately.
    Version compare only when the local mapped/found filename ends in a date.
    """
    if not probed:
        return "unknown"
    want = iso_to_date_stamp(remote_date)
    if not want:
        return "unknown"
    _path, status = resolve_local_archive(mo2_root, dep, remote_date=want)
    if status == "stale":
        return "new"
    if status == "current":
        return "current"
    return "unknown"


def find_archive_for_dep(
    mo2_root: Path,
    dep: Dependency,
    *,
    remote_date: str = "",
) -> Path | None:
    path, _status = resolve_local_archive(mo2_root, dep, remote_date=remote_date)
    return path


def _is_archive_file(path: Path) -> bool:
    if not path.is_file():
        return False
    low = path.name.lower()
    # MO2 in-progress downloads — never claim these.
    if low.endswith(".unfinished") or ".unfinished." in low:
        return False
    if path.suffix.lower() in ARCHIVE_SUFFIXES:
        return True
    # MO2 sometimes saves ModDB CDN hits as extensionless hash filenames.
    return bool(_archive_magic_suffix(path))


def _archive_magic_suffix(path: Path) -> str:
    """Return .zip/.7z/.rar if file magic matches; else \"\"."""
    try:
        with path.open("rb") as fh:
            head = fh.read(8)
    except OSError:
        return ""
    if head.startswith(b"PK"):
        return ".zip"
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return ".7z"
    if head.startswith(b"Rar!\x1a\x07"):
        return ".rar"
    return ""


def claim_download_as_zip(
    mo2_root: Path,
    dep: Dependency,
    *,
    moddb_filename: str = "",
    date: str = "",
    dry_run: bool = False,
    file_id: str = "",
    source_url: str = "",
) -> Path | None:
    """Move/rename a loose archive into downloads/DOGMA/<id>[ date]{ext}."""
    with _downloads_claim_lock:
        return _claim_download_as_zip_unlocked(
            mo2_root,
            dep,
            moddb_filename=moddb_filename,
            date=date,
            dry_run=dry_run,
            file_id=file_id,
            source_url=source_url,
        )


def _claim_download_as_zip_unlocked(
    mo2_root: Path,
    dep: Dependency,
    *,
    moddb_filename: str = "",
    date: str = "",
    dry_run: bool = False,
    file_id: str = "",
    source_url: str = "",
) -> Path | None:
    stem = dep_zip_stem(dep, date=date)
    dld = downloads_dir(mo2_root)
    # Already claimed under the target stem
    existing = [
        p
        for p in (dld.iterdir() if dld.is_dir() else [])
        if _is_archive_file(p) and p.stem.lower() == stem.lower()
    ]
    if existing:
        return _prefer_archive(existing)

    # Also scan MO2 downloads/ root (and a leftover DOGMA-downloads/) for loose drops.
    top = mo2_root / "downloads"
    side = Path(mo2_root) / "DOGMA-downloads"
    needles: list[str] = []
    if moddb_filename:
        needles.append(Path(moddb_filename).name.lower())
        needles.append(Path(moddb_filename).stem.lower())
    needles.append(dep.id.lower())
    # Spaced ids also match zips as alphanumeric mush (e.g. "Screen Space Shaders")
    id_alnum = re.sub(r"[^a-z0-9]+", "", dep.id.lower())
    if id_alnum and id_alnum != dep.id.lower():
        needles.append(id_alnum)
    fid = str(file_id or "").strip()
    # MO2 often names ModDB CDN hits as the mirror URL's trailing hex hash.
    src_url = (source_url or "").strip().lower()
    url_hash = ""
    if src_url:
        m = re.search(r"/([a-f0-9]{16,40})(?:/|$|\?)", src_url)
        if m:
            url_hash = m.group(1)
            needles.append(url_hash)

    def _meta_text(p: Path) -> str:
        meta = Path(str(p) + ".meta")
        if not meta.is_file():
            meta = p.with_suffix(p.suffix + ".meta")
        if not meta.is_file():
            return ""
        try:
            return meta.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            return ""

    def _matches_this_download(p: Path) -> bool:
        """True when ``p`` is the archive for this dep/url (not another CDN hit)."""
        low = p.name.lower()
        stem_low = p.stem.lower()
        # Prefer exact ModDB CDN hash from the URL we just downloaded.
        if url_hash:
            if low == url_hash or stem_low == url_hash:
                return True
            if low.startswith(url_hash + ".") or stem_low.startswith(url_hash):
                return True
            meta_txt = _meta_text(p)
            if url_hash in meta_txt or (src_url and src_url in meta_txt):
                return True
            # With a known CDN hash, do not fuzzy-match other downloads.
            return False
        if any(n and (n == low or n == stem_low or n in stem_low) for n in needles):
            return True
        meta_txt = _meta_text(p)
        if not meta_txt:
            return False
        if fid and fid in meta_txt:
            return True
        if any(n and n in meta_txt for n in needles if len(n) >= 6):
            return True
        if "moddb.com" in meta_txt and any(
            n.replace(" ", "") in meta_txt.replace(" ", "")
            for n in needles
            if len(n) >= 8
        ):
            return True
        return False

    candidates: list[Path] = []
    scan_roots = (dld, side, top)
    for root in scan_roots:
        if not root.is_dir():
            continue
        for p in root.iterdir():
            if p.name.lower().endswith(".meta"):
                continue
            if not _is_archive_file(p):
                continue
            if p.parent.resolve() == dld.resolve() and p.stem.lower() == stem.lower():
                return p
            # Skip other DOGMA dated/pinned copies of this id (not the fresh download)
            if p.parent.resolve() == dld.resolve():
                la = None
                for item in list_dep_archives(mo2_root, dep):
                    if item.path.resolve() == p.resolve():
                        la = item
                        break
                if la is not None:
                    continue
            if _matches_this_download(p):
                candidates.append(p)

    # Last resort (no URL hash): newest archive in downloads/ from the last few minutes.
    if not candidates and not url_hash and top.is_dir():
        import time

        now = time.time()
        recent: list[Path] = []
        for p in top.iterdir():
            if p.is_dir():
                continue
            if p.parent.resolve() == dld.resolve():
                continue
            if p.name.lower().endswith(".meta"):
                continue
            if not _is_archive_file(p):
                continue
            try:
                if now - p.stat().st_mtime <= 180:
                    recent.append(p)
            except OSError:
                continue
        recent.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        candidates = recent[:1]

    if not candidates:
        return None

    def _claim_rank(p: Path) -> tuple[int, float]:
        low = p.name.lower()
        stem_low = p.stem.lower()
        # Exact CDN hash name first, then newer mtime.
        exact = 0 if url_hash and (low == url_hash or stem_low == url_hash) else 1
        try:
            mtime = -p.stat().st_mtime
        except OSError:
            mtime = 0.0
        return (exact, mtime)

    candidates.sort(key=_claim_rank)
    src = candidates[0]
    suffix = src.suffix.lower() if src.suffix else _archive_magic_suffix(src)
    if not suffix or suffix == ".unfinished":
        suffix = ".zip"
    dest = dld / f"{stem}{suffix}"
    if src.resolve() == dest.resolve():
        return dest
    info(f"  [{dep.id}] claim download -> downloads/DOGMA/{dest.name}")
    if dry_run:
        return dest
    if dest.exists() and dest.resolve() != src.resolve():
        dest.unlink()
    _move_download_file(src, dest)
    meta = Path(str(src) + ".meta")
    if not meta.is_file():
        meta = src.with_suffix(src.suffix + ".meta")
    if meta.is_file():
        try:
            meta.unlink()
        except OSError:
            pass
    return dest


def _move_download_file(src: Path, dest: Path) -> None:
    """``shutil.move`` with short retries for Windows file locks."""
    import time

    last: BaseException | None = None
    for attempt in range(8):
        try:
            shutil.move(str(src), str(dest))
            return
        except OSError as exc:
            last = exc
            # WinError 32: sharing violation — another process may still hold the file.
            if getattr(exc, "winerror", None) != 32 and exc.errno not in (
                11,
                13,
                16,
            ):
                raise
            time.sleep(0.25 * (attempt + 1))
    assert last is not None
    raise last


def _guess_archive_suffix(
    *,
    filename: str = "",
    url: str = "",
    path: Path | None = None,
) -> str:
    """Pick .zip/.7z/.rar from a name, URL path, or file magic."""
    from urllib.parse import unquote, urlparse

    for raw in (filename, unquote(Path(urlparse(url).path).name) if url else ""):
        suf = Path(raw).suffix.lower()
        if suf in ARCHIVE_SUFFIXES:
            return suf if suf != ".7zip" else ".7z"
    if path is not None:
        magic = _archive_magic_suffix(path)
        if magic:
            return magic
    return ".zip"


def _filename_from_content_disposition(header: str) -> str:
    if not header:
        return ""
    from urllib.parse import unquote

    m = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", header, re.I)
    if m:
        return Path(unquote(m.group(1).strip().strip('"'))).name
    m = re.search(r'filename\s*=\s*"([^"]+)"', header, re.I)
    if m:
        return Path(m.group(1)).name
    m = re.search(r"filename\s*=\s*([^;]+)", header, re.I)
    if m:
        return Path(m.group(1).strip().strip('"')).name
    return ""


def http_download_file(
    url: str,
    dest: Path,
    *,
    referer: str = "",
    timeout: int = 180,
    on_progress: Callable[[int, int], None] | None = None,
) -> str:
    """Download ``url`` to ``dest`` (via ``.partial``). Returns suggested filename.

    Does not use Mod Organizer. Follows redirects. Rejects HTML bodies.
    ``on_progress(bytes_done, bytes_total)`` — total is 0 when Content-Length is absent.
    """
    import urllib.error
    import urllib.request
    from urllib.parse import unquote, urlparse

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".partial")
    if partial.exists():
        try:
            partial.unlink()
        except OSError:
            pass

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36 DOGMA/1.0"
        ),
        "Accept": "*/*",
    }
    if referer:
        headers["Referer"] = referer

    info(f"  HTTP download: {url}")
    req = urllib.request.Request(url, headers=headers)
    suggested = ""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            suggested = _filename_from_content_disposition(
                resp.headers.get("Content-Disposition") or ""
            )
            if not suggested:
                suggested = unquote(Path(urlparse(resp.geturl()).path).name)
            ctype = (resp.headers.get("Content-Type") or "").lower()
            try:
                total = int(resp.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                total = 0
            done = 0
            if on_progress is not None:
                on_progress(0, total)
            with partial.open("wb") as out:
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if on_progress is not None:
                        on_progress(done, total)
            if "text/html" in ctype or _looks_like_html_file(partial):
                try:
                    partial.unlink()
                except OSError:
                    pass
                raise RuntimeError(
                    f"download returned HTML, not an archive: {url}"
                )
            if on_progress is not None:
                on_progress(done if done else total, total if total else done)
    except urllib.error.HTTPError as exc:
        try:
            partial.unlink()
        except OSError:
            pass
        raise RuntimeError(f"HTTP {exc.code} downloading {url}") from exc
    except urllib.error.URLError as exc:
        try:
            partial.unlink()
        except OSError:
            pass
        raise RuntimeError(f"download failed for {url}: {exc.reason}") from exc

    if dest.exists():
        dest.unlink()
    _move_download_file(partial, dest)
    ok(f"  HTTP download saved: {dest.name}")
    return Path(suggested).name if suggested else dest.name


def ensure_dep_archive(
    mo2_root: Path,
    dep: Dependency,
    *,
    download_url: str,
    moddb: ModdbInfo | None,
    dry_run: bool,
    github: GithubInfo | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[Path | None, str]:
    """Ensure downloads/DOGMA has the right archive; download if missing/stale.

    Returns (archive_path, note) where note is pinned|current|downloaded|missing|…
    Undated <id>.* is pinned and never replaced.
    """
    remote_date = moddb_date_stamp(moddb) or github_date_stamp(github)
    remote_filename = (moddb.filename if moddb else "") or (
        github.filename if github else ""
    )
    archive, status = resolve_local_archive(mo2_root, dep, remote_date=remote_date)
    if status == "pinned":
        info(f"  [{dep.id}] archive pinned (undated): {archive.name}")
        return archive, "pinned"
    if status == "current" and archive:
        return archive, "current"

    # stale or missing — need remote date for a stable name; fall back to undated id
    target_date = remote_date
    if not target_date and status == "missing":
        # No remote date: claim as undated <id>.*
        target_date = ""

    if dep.source == "user":
        if not archive:
            return None, "manual-missing"
        return archive, "pinned" if status == "pinned" else status

    if not download_url:
        return archive, status

    if status == "stale" and remote_date:
        info(
            f"  [{dep.id}] archive stale "
            f"(have {archive.name if archive else '?'}; remote {remote_date})"
        )
    elif status == "missing":
        info(f"  [{dep.id}] archive missing; downloading")

    if dry_run:
        claimed = claim_download_as_zip(
            mo2_root,
            dep,
            moddb_filename=remote_filename,
            date=target_date,
            dry_run=True,
            file_id=(moddb.file_id if moddb else ""),
            source_url=download_url,
        )
        return claimed or archive, "would-download"

    dld = downloads_dir(mo2_root)
    stem = dep_zip_stem(dep, date=target_date)
    suffix = _guess_archive_suffix(filename=remote_filename, url=download_url)
    dest = dld / f"{stem}{suffix}"
    tmp = dld / f".{stem}.downloading"
    referer = ""
    if moddb is not None and (moddb.start_url or "").strip():
        referer = moddb.start_url.strip()
    elif is_moddb_url(download_url):
        referer = "https://www.moddb.com/"

    try:
        suggested = http_download_file(
            download_url,
            tmp,
            referer=referer,
            on_progress=on_progress,
        )
        magic = _archive_magic_suffix(tmp)
        if magic:
            suffix = magic
        else:
            suffix = _guess_archive_suffix(
                filename=suggested or remote_filename,
                url=download_url,
                path=tmp,
            )
        dest = dld / f"{stem}{suffix}"
        if dest.exists() and dest.resolve() != tmp.resolve():
            dest.unlink()
        _move_download_file(tmp, dest)
        set_archive_map_entry(mo2_root, dep.id, dest.name)
        return dest, "downloaded"
    except (RuntimeError, OSError, ValueError) as exc:
        warn(f"  [{dep.id}] direct download failed ({exc}); scanning for archive…")
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass

    # Fallback: claim a loose file already in downloads/DOGMA or MO2 downloads/.
    claimed = claim_download_as_zip(
        mo2_root,
        dep,
        moddb_filename=remote_filename,
        date=target_date,
        dry_run=False,
        file_id=(moddb.file_id if moddb else ""),
        source_url=download_url,
    )
    if claimed:
        set_archive_map_entry(mo2_root, dep.id, claimed.name)
        return claimed, "downloaded"
    if moddb is not None:
        _purge_moddb_start_stubs(mo2_root, file_id=moddb.file_id)
    if archive:
        warn(f"  [{dep.id}] download failed; keeping {archive.name}")
        return archive, status
    if moddb is not None and re.search(
        r"(?i)/(?:addons|downloads)/start/\d+", download_url
    ):
        return None, "missing-moddb-interstitial"
    return None, "missing"


def extract_archive(archive: Path, dest: Path) -> None:
    info(f"  extract: {archive.name} -> {dest}")
    dest.mkdir(parents=True, exist_ok=True)
    suffix = archive.suffix.lower()
    if suffix == ".zip":
        with zipfile.ZipFile(archive, "r") as zf:
            zf.extractall(dest)
        ok(f"  extract OK (zip): {archive.name}")
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
        err(f"  7z stderr: {(proc.stderr or proc.stdout or '').strip()}")
        raise RuntimeError(f"7z extract failed for {archive.name}: {proc.stderr or proc.stdout}")
    ok(f"  extract OK (7z): {archive.name}")


def install_selected_feature_packages(
    mo2_root: Path,
    data: ManifestData,
    selected_option_ids: Iterable[str],
    *,
    dry_run: bool = False,
) -> list[str]:
    """Extract selected feature package zips into mods/DOGMA.

    Also unpacks nested feature requires. Returns feature paths unpacked.
    """
    dest = dogma_mod_dir(mo2_root)
    packages = feature_packages_dir(mo2_root)
    unpacked: list[str] = []
    for feat in expand_feature_package_selection(data, selected_option_ids):
        meta = data.features[feat]
        zpath = packages / f"{feature_path_key(feat)}.zip"
        if not zpath.is_file():
            warn(
                f"  feature package missing for {meta.display_name!r} "
                f"(expected {zpath.name})"
            )
            continue
        unpacked.append(feat)
        if dry_run:
            info(f"  would extract feature package: {zpath.name} -> {dest}")
            continue
        info(f"  feature package: {meta.display_name} ({zpath.name})")
        extract_archive(zpath, dest)
    return unpacked


def preview_feature_effect_sections(
    meta: FeatureMeta,
    *,
    mo2_root: Path | None = None,
) -> list[tuple[str, list[str]]]:
    """Wizard/FOMOD preview rows for a feature's own disables/enables/etc."""
    sections: list[tuple[str, list[str]]] = []
    if meta.disables:
        sections.append(("Disables", list(meta.disables)))
    if meta.enables:
        sections.append(("Enables", list(meta.enables)))
    if meta.deletes:
        sections.append(
            ("Deletes", [format_preview_path(mo2_root, p) for p in meta.deletes])
        )
    if meta.moves:
        sections.append(
            (
                "Moves",
                [
                    f"{a} → {format_preview_path(mo2_root, b)}"
                    for a, b in meta.moves
                ],
            )
        )
    if meta.resets:
        sections.append(("Resets MCM", list(meta.resets)))
    if meta.mcm:
        sections.append(("MCM", [f"{k}={v}" for k, v in meta.mcm.items()]))
    if meta.settings:
        sections.append(
            ("Settings", [f"{k}={v}" for k, v in meta.settings.items()])
        )
    return sections


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


def _process_path_dependency(
    mo2_root: Path,
    modlist: Path,
    dep: Dependency,
    *,
    mode: str,
    dry_run: bool,
) -> str:
    """Install a local path mod by extracting mo2/packages/<path_key>.zip into DOGMA."""
    path = dep.path.replace("\\", "/")
    dest = dogma_mod_dir(mo2_root)
    installed = detect_installed_features(mo2_root, [path])
    present = path in installed
    packages = feature_packages_dir(mo2_root)
    zpath = packages / f"{feature_path_key(path)}.zip"

    if present and mode != "reinstall":
        info(f"  [{dep.id}] path mod already in DOGMA ({path})")
        run_dep_side_effects(mo2_root, dep, dest if dest.is_dir() else None, dry_run=dry_run)
        return "present"

    if not zpath.is_file():
        if present:
            info(f"  [{dep.id}] package missing but markers present; applying effects")
            run_dep_side_effects(mo2_root, dep, dest if dest.is_dir() else None, dry_run=dry_run)
            return "present"
        warn(
            f"  [{dep.id}] feature package missing "
            f"(expected {zpath.as_posix()})"
        )
        return "missing-package"

    if dry_run:
        info(f"  [{dep.id}] would extract {zpath.name} -> {dest}")
        run_dep_side_effects(mo2_root, dep, dest, dry_run=True)
        return "would-install"

    info(f"  [{dep.id}] path package: {zpath.name} -> DOGMA")
    extract_archive(zpath, dest)
    run_dep_side_effects(mo2_root, dep, dest, dry_run=False)
    ok(f"  [{dep.id}] path mod installed into DOGMA")
    return "installed"


def process_dependency(
    mo2_root: Path,
    modlist: Path,
    dep: Dependency,
    *,
    mode: str,
    dry_run: bool,
) -> str:
    """mode: reinstall | ensure. Returns status string."""
    if dep.path:
        return _process_path_dependency(
            mo2_root, modlist, dep, mode=mode, dry_run=dry_run
        )

    tools = mo2_tools_dir(mo2_root)
    moddb: ModdbInfo | None = None
    github: GithubInfo | None = None
    download_url = dep.url
    if dep.url and is_moddb_url(dep.url) and dep.source != "user":
        try:
            moddb = resolve_moddb(dep.url, cache_dir=tools)
            download_url = moddb.start_url or dep.url
            bits = []
            if moddb.filename:
                bits.append(moddb.filename)
            stamp = moddb_date_stamp(moddb)
            if stamp:
                bits.append(f"date {stamp}")
            elif moddb.updated or moddb.added:
                bits.append(f"updated {moddb.updated or moddb.added}")
            if bits:
                info(f"  [{dep.id}] ModDB: {', '.join(bits)}")
            try:
                mirror = resolve_moddb_mirror_url(download_url)
                if mirror and mirror.rstrip("/") != download_url.rstrip("/"):
                    info(f"  [{dep.id}] ModDB mirror: {mirror}")
                    download_url = mirror
            except RuntimeError as exc:
                warn(f"  [{dep.id}] ModDB mirror resolve failed ({exc})")
        except (RuntimeError, ValueError, OSError) as exc:
            warn(f"  [{dep.id}] ModDB resolve failed ({exc}); using manifest URL")
    elif dep.url and is_github_url(dep.url) and dep.source != "user":
        try:
            github = resolve_github(
                dep.url,
                archive_name=dep.archive_name,
                cache_dir=tools,
            )
            download_url = github.download_url or dep.url
            bits = [github.kind or "github"]
            if github.tag:
                bits.append(github.tag)
            if github.filename:
                bits.append(github.filename)
            stamp = github_date_stamp(github)
            if stamp:
                bits.append(f"date {stamp}")
            info(f"  [{dep.id}] GitHub: {', '.join(bits)}")
        except (RuntimeError, ValueError, OSError) as exc:
            warn(f"  [{dep.id}] GitHub resolve failed ({exc}); using manifest URL")

    ok_present, folders = dep_is_satisfied(mo2_root, dep, moddb=moddb)
    catalog = find_catalog_folders_for_urls(mo2_root, dep_url_candidates(dep, moddb))

    if catalog:
        # Never wipe catalog — enable only
        enabled = enable_mods_in_modlist(modlist, catalog, dry_run)
        msg = f"catalog enable {catalog}" + (f" (newly: {enabled})" if enabled else " (already on)")
        info(f"  [{dep.id}] {msg}")
        run_dep_side_effects(mo2_root, dep, None, dry_run=dry_run)
        return "catalog"

    managed = find_managed_folders(mo2_root, dep)
    if mode == "reinstall":
        for name in managed:
            info(f"  [{dep.id}] wipe managed: {name}")
            wipe_managed_mod(mo2_root, modlist, name, dry_run)
        managed = []
        ok_present = False

    # Keep / refresh archive first (pinned undated never replaced; dated may update)
    archive, arch_note = ensure_dep_archive(
        mo2_root,
        dep,
        download_url=download_url or "",
        moddb=moddb,
        github=github,
        dry_run=dry_run,
    )
    remote_date = moddb_date_stamp(moddb) or github_date_stamp(github)

    def _managed_dir() -> Path | None:
        if not managed:
            return None
        return mo2_root / "mods" / managed[0]

    if arch_note == "manual-missing":
        if managed:
            enable_mods_in_modlist(modlist, managed, dry_run)
            info(f"  [{dep.id}] already present (manual): {managed}")
            run_dep_side_effects(mo2_root, dep, _managed_dir(), dry_run=dry_run)
            return "present"
        if ok_present and folders:
            enable_mods_in_modlist(modlist, folders, dry_run)
            info(f"  [{dep.id}] already present (manual): {folders}")
            run_dep_side_effects(mo2_root, dep, None, dry_run=dry_run)
            return "present"
        hint = f"downloads/DOGMA/{dep_zip_stem(dep)}.zip"
        howto = dep.howto or (
            f"no url: (manual) — place {hint} or install yourself; "
            f"disables/enables skipped until then"
        )
        info(f"  [{dep.id}] skip (manual, not installed): {howto}")
        return "manual-missing"

    need_install = mode == "reinstall" or not managed
    if managed and arch_note in ("downloaded", "would-download"):
        # Newer dated zip arrived — reinstall managed mod from it
        info(f"  [{dep.id}] newer archive; reinstalling managed mod")
        for name in managed:
            wipe_managed_mod(mo2_root, modlist, name, dry_run)
        managed = []
        need_install = True

    if managed and not need_install:
        enable_mods_in_modlist(modlist, managed, dry_run)
        info(f"  [{dep.id}] already present: {managed}")
        run_dep_side_effects(mo2_root, dep, _managed_dir(), dry_run=dry_run)
        return "present"

    if ok_present and folders and not need_install:
        enable_mods_in_modlist(modlist, folders, dry_run)
        info(f"  [{dep.id}] already present: {folders}")
        run_dep_side_effects(mo2_root, dep, None, dry_run=dry_run)
        return "present"

    if not archive:
        raise FileNotFoundError(
            f"[{dep.id}] archive not found "
            f"(expected downloads/DOGMA/{dep_zip_stem(dep, date=remote_date)}.zip|.7z|…). "
            f"URL={download_url}"
        )

    folder_name = f"{MANAGED_FOLDER_PREFIX}{dep.id}"
    dest = mo2_root / "mods" / folder_name
    info(f"  [{dep.id}] install -> {folder_name} from {archive.name}")
    if dry_run:
        run_dep_side_effects(mo2_root, dep, dest, dry_run=True)
        return "would-install"
    if dest.exists():
        shutil.rmtree(dest)
    extract_archive(archive, dest)
    normalize_extracted_mod(dest)
    write_meta_url(dest, managed_stamp_for(dep))
    info(f"  [{dep.id}] meta stamp written")
    insert_mod_under_separator(modlist, folder_name, dry_run=False)
    run_after_unpack(mo2_root, dep, dest)
    run_dep_side_effects(mo2_root, dep, dest, dry_run=False)
    ok(f"  [{dep.id}] installed -> {folder_name}")
    return "installed"


def gather_dep_disable_rules(
    mo2_root: Path,
    deps: list[Dependency],
    modlist: Path,
) -> list[Rule]:
    rules: list[Rule] = []
    seen: set[str] = set()
    for dep in deps:
        if not dep.disables:
            continue
        if not dep_effects_active(mo2_root, dep, modlist):
            if dep.source == "user" or not dep.url:
                info(f"  skip disables for [{dep.id}] (manual / not installed)")
            continue
        for name in _unique_strs(dep.disables):
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            rules.extend(rules_from_disable_names([name], source=f"dep:{dep.id}"))
    return rules


def gather_dep_enable_rules(
    mo2_root: Path,
    deps: list[Dependency],
    modlist: Path,
) -> list[Rule]:
    """Enable rules from satisfied deps (downloads / suggested)."""
    rules: list[Rule] = []
    seen: set[str] = set()
    for dep in deps:
        if not dep.enables:
            continue
        if not dep_effects_active(mo2_root, dep, modlist):
            if dep.source == "user" or not dep.url:
                info(f"  skip enables for [{dep.id}] (manual / not installed)")
            continue
        for name in _unique_strs(dep.enables):
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            rules.extend(rules_from_disable_names([name], source=f"dep:{dep.id}"))
    return rules


# ---------------------------------------------------------------------------
# validate / report
# ---------------------------------------------------------------------------

def build_report(
    mo2_root: Path,
    cfg: Path,
    *,
    tier: str = "downloads",
    profile: str = "",
    deps: list[Dependency] | None = None,
) -> Path:
    tools = mo2_tools_dir(mo2_root)
    tools.mkdir(parents=True, exist_ok=True)
    report_path = report_log_path(tools)
    report_path.parent.mkdir(parents=True, exist_ok=True)
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
        W("PyYAML not importable — run DOGMA Setup once")
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

    # Catalog: features.yml + mods.yml
    try:
        man_path = resolve_manifest_path(cfg)
        data = load_manifest(man_path)
        installed = resolve_installed_features(mo2_root, data)
        lines.append(f"Catalog : {man_path}")
        opts = resolve_options_path(cfg)
        mods = resolve_mods_path(cfg)
        sug = resolve_suggestions_path(cfg)
        if opts:
            lines.append(f"Options : {opts}")
        if mods:
            lines.append(f"Mods    : {mods}")
        if sug and not (opts or mods):
            lines.append(f"Suggestions: {sug}")
        if installed is not None:
            feat_n = len([f for f in installed if f.lower() != "common"])
            lines.append(f"Installed DOGMA features detected: {feat_n}")
        packages = resolve_feature_packages_dir(
            mo2_root=mo2_root, catalog_path=man_path
        )
        if packages is not None:
            lines.append(f"Packages: {packages}")
            for feat, meta in sorted(
                data.features.items(), key=lambda kv: kv[1].display_name.lower()
            ):
                if meta.always_on or meta.stage != "release":
                    continue
                zpath = packages / f"{feature_path_key(feat)}.zip"
                if zpath.is_file():
                    O(f'release feature package present: {zpath.name}')
                else:
                    W(
                        f'release feature "{meta.display_name}" ({feat}) '
                        f"missing package zip ({zpath.name})"
                    )
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
        check = list(deps) if deps is not None else filter_deps(
            data, tier, installed=installed
        )
        # Only validate packs that actually install (url / buy / enables work).
        check = [
            d
            for d in check
            if d.url or d.buy_url or d.source == "user" or d.enables or d.has_install_work()
        ]
        seen_check: set[str] = set()
        uniq_check: list[Dependency] = []
        for d in check:
            if d.id in seen_check:
                continue
            seen_check.add(d.id)
            uniq_check.append(d)
        check = uniq_check
        for dep in check:
            moddb = None
            if dep.url and is_moddb_url(dep.url) and dep.source != "user":
                try:
                    moddb = resolve_moddb(dep.url, cache_dir=tools)
                    lines.append(
                        f'ModDB "{dep.id}": file={moddb.filename or "?"} '
                        f"updated={moddb.updated or '?'} "
                        f"start={moddb.start_url or '?'}"
                    )
                except (RuntimeError, ValueError, OSError) as exc:
                    W(f'ModDB resolve failed for "{dep.id}": {exc}')
            sat, folders = dep_is_satisfied(
                mo2_root, dep, modlist, require_enabled=True, moddb=moddb
            )
            present, _ = dep_is_satisfied(
                mo2_root, dep, require_enabled=False, moddb=moddb
            )
            if not present:
                if dep.source == "user" or not dep.url:
                    O(
                        f'mod "{dep.id}" manual (no url:) — not installed; '
                        f"disables/enables skipped"
                    )
                else:
                    stamp = moddb_date_stamp(moddb)
                    arch, st = resolve_local_archive(
                        mo2_root, dep, remote_date=stamp
                    )
                    if st == "missing":
                        want = dep_zip_stem(dep, date=stamp) + ".*"
                        W(f'mod "{dep.id}" archive missing (want downloads/DOGMA/{want})')
                    elif st == "stale":
                        W(
                            f'mod "{dep.id}" archive stale: have '
                            f"{arch.name if arch else '?'}; ModDB date {stamp or '?'}"
                        )
                    else:
                        W(f'{dep.tier} mod "{dep.id}" is missing')
            elif not sat:
                W(f'{dep.tier} mod "{dep.id}" is installed but disabled ({folders})')
            else:
                O(f'mod "{dep.id}" satisfied via {folders}')
                dep_rules = rules_from_disable_names(dep.disables, source=f"mod:{dep.id}")
                for name in sorted(enabled):
                    rule = mod_matches(name, dep_rules)
                    if rule:
                        W(
                            f'"{name}" is enabled but mod "{dep.id}" '
                            f"lists it as a conflict"
                        )
                dep_en = rules_from_disable_names(dep.enables, source=f"mod:{dep.id}")
                for name in sorted(disabled_names):
                    rule = mod_matches(name, dep_en)
                    if rule:
                        W(
                            f'"{name}" is disabled but mod "{dep.id}" '
                            f"lists it to enable"
                        )
    except Exception as exc:  # noqa: BLE001
        W(f"catalog error: {exc}")

    lines.append("")
    lines.append(f"Summary: {warns} WARN, {oks} OK")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    append_action_log(tools, f"report written -> {report_path} ({warns} WARN, {oks} OK)")
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

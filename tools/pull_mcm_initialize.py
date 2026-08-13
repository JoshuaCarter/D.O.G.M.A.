#!/usr/bin/env python3
"""Removed. 3rd-party MCM lives in config/mcm_config.yml (in-game Defaults tab)."""

from __future__ import annotations

import sys


def main() -> int:
    sys.stderr.write(
        "pull_mcm_initialize.py is removed.\n"
        "Edit config/mcm_config.yml by hand. Build snapshots "
        "appdata/axr_options.ltx via tools/gen_defaults.py.\n"
        "Do not write mcm_set: into manifests.\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

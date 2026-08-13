#!/usr/bin/env python3
"""Removed. mcm_config.yml is hand-edited; values come from the build snapshot."""

from __future__ import annotations

import sys


def main() -> int:
    sys.stderr.write(
        "pull_mcm_config.py is removed.\n"
        "Edit config/mcm_config.yml by hand. Build snapshots "
        "appdata/axr_options.ltx via tools/gen_defaults.py.\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

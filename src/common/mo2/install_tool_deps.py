#!/usr/bin/env python3
"""Ensure Python tooling deps for DOGMA MO2 jobs (PyYAML, etc.)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    here = Path(__file__).resolve().parent
    # Re-exec via dogma_job setup so logic stays in one place
    job = here / "dogma_job.py"
    return subprocess.call([sys.executable, str(job), "setup", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())

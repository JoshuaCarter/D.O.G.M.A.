"""Entry: python -m sage  OR  py -3 dev/sage/__main__.py"""

from __future__ import annotations

import sys
from pathlib import Path

_DEV = Path(__file__).resolve().parent.parent
if str(_DEV) not in sys.path:
    sys.path.insert(0, str(_DEV))

from sage.app import main

if __name__ == "__main__":
    raise SystemExit(main())

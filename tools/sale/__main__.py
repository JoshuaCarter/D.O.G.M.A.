"""python -m sale"""

from __future__ import annotations

import sys
from pathlib import Path

_DEV = Path(__file__).resolve().parents[1]
if str(_DEV) not in sys.path:
    sys.path.insert(0, str(_DEV))

from sale.app import main

raise SystemExit(main())

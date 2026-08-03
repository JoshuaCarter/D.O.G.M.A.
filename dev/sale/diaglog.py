"""File logger for SALE."""

from __future__ import annotations

import logging
from pathlib import Path

_LOG_PATH = Path(__file__).resolve().parent / "sale.log"
_configured = False


def setup_logging() -> None:
    global _configured
    if _configured:
        return
    root = logging.getLogger("sale")
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    fh = logging.FileHandler(_LOG_PATH, encoding="utf-8")
    fh.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root.addHandler(fh)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(f"sale.{name}")

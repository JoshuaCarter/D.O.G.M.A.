"""Heavy file logging for SAGE diagnostics.

Single append-only log: ``dev/sage/sage.log`` (Python + Qt + faulthandler).
Import ``get_logger`` / call ``setup_logging`` early in process startup.
"""

from __future__ import annotations

import faulthandler
import logging
import sys
import threading
import traceback
from pathlib import Path
from typing import Any

_PKG_DIR = Path(__file__).resolve().parent
LOG_PATH = _PKG_DIR / "sage.log"

_LOGGER_NAME = "sage"
_configured = False
_fault_fp: Any = None


def log_path() -> Path:
    return LOG_PATH


def setup_logging() -> logging.Logger:
    """Configure package logger → append-only ``sage.log``. Idempotent."""
    global _configured, _fault_fp
    log = logging.getLogger(_LOGGER_NAME)
    if _configured:
        return log

    log.setLevel(logging.DEBUG)
    log.propagate = False

    handler = logging.FileHandler(LOG_PATH, mode="a", encoding="utf-8", delay=False)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s.%(msecs)03d %(levelname)-7s "
            "[%(threadName)s] %(name)s.%(funcName)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    log.addHandler(handler)

    # Native crash traces into the same file (segfaults / Qt aborts that still dump).
    try:
        _fault_fp = open(LOG_PATH, "a", encoding="utf-8")  # noqa: SIM115
        faulthandler.enable(file=_fault_fp, all_threads=True)
    except OSError:
        faulthandler.enable(all_threads=True)

    _install_excepthooks(log)
    _install_qt_message_handler(log)

    _configured = True
    log.info("=== SAGE diagnostics logging started -> %s ===", LOG_PATH)
    log.debug("Python %s | executable %s", sys.version.replace("\n", " "), sys.executable)
    return log


def get_logger(name: str | None = None) -> logging.Logger:
    """Return ``sage`` or ``sage.<name>`` logger (setup_logging if needed)."""
    if not _configured:
        setup_logging()
    if not name:
        return logging.getLogger(_LOGGER_NAME)
    return logging.getLogger(f"{_LOGGER_NAME}.{name}")


def _install_excepthooks(log: logging.Logger) -> None:
    prev = sys.excepthook

    def _hook(exc_type, exc, tb) -> None:  # noqa: ANN001
        log.critical(
            "Unhandled exception\n%s",
            "".join(traceback.format_exception(exc_type, exc, tb)),
        )
        if prev is not None:
            prev(exc_type, exc, tb)

    sys.excepthook = _hook

    if hasattr(threading, "excepthook"):

        def _thread_hook(args) -> None:  # noqa: ANN001
            if args.exc_type is SystemExit:
                return
            log.critical(
                "Unhandled thread exception (%s)\n%s",
                args.thread.name if args.thread else "?",
                "".join(
                    traceback.format_exception(
                        args.exc_type, args.exc_value, args.exc_traceback
                    )
                ),
            )

        threading.excepthook = _thread_hook  # type: ignore[assignment]


def _install_qt_message_handler(log: logging.Logger) -> None:
    try:
        from PyQt6.QtCore import QtMsgType, qInstallMessageHandler
    except Exception:  # noqa: BLE001
        return

    level_map = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }

    def _qt_handler(mode, context, message) -> None:  # noqa: ANN001
        lvl = level_map.get(mode, logging.WARNING)
        where = ""
        if context is not None:
            file = getattr(context, "file", None) or ""
            line = getattr(context, "line", 0) or 0
            func = getattr(context, "function", None) or ""
            if file or func:
                where = f" ({file}:{line} {func})"
        log.log(lvl, "Qt%s: %s", where, message)

    try:
        qInstallMessageHandler(_qt_handler)
    except Exception:  # noqa: BLE001
        log.debug("qInstallMessageHandler failed", exc_info=True)

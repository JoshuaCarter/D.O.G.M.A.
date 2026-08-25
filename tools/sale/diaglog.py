"""SALE diagnostics: ``sale.log`` + optional Log tab mirror.

Captures Python exceptions, thread failures, native fatal signals (faulthandler),
and Qt messages. Format includes thread / function / line for crash triage.
"""

from __future__ import annotations

import faulthandler
import logging
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Callable

_PKG_DIR = Path(__file__).resolve().parent
LOG_PATH = _PKG_DIR / "sale.log"
_LOGGER_NAME = "sale"

_configured = False
_fault_fp: Any = None
_ui_handler: "UiLogHandler | None" = None

_ASCII_TRANS = str.maketrans(
    {
        "\u2192": "->",
        "\u2190": "<-",
        "\u00d7": "x",
        "\u2026": "...",
        "\u2014": "-",
        "\u2013": "-",
        "\u00b7": "|",
        "\u26a0": "!",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u00a0": " ",
    }
)


def ascii_only(text: str) -> str:
    if not text:
        return text
    return text.translate(_ASCII_TRANS).encode("ascii", errors="replace").decode("ascii")


class _AsciiFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return ascii_only(super().format(record))


class UiLogHandler(logging.Handler):
    """Buffer + optional Qt sink (thread-safe)."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.setFormatter(
            _AsciiFormatter(
                "%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        self._lock = threading.Lock()
        self._buffer: list[str] = []
        self._emit_line: Callable[[str], None] | None = None
        self._max = 2000

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:  # noqa: BLE001
            return
        with self._lock:
            sink = self._emit_line
            if sink is None:
                self._buffer.append(msg)
                if len(self._buffer) > self._max:
                    self._buffer = self._buffer[-self._max :]
                return
        try:
            sink(msg)
        except Exception:  # noqa: BLE001
            pass

    def attach(self, emit_line: Callable[[str], None]) -> list[str]:
        with self._lock:
            self._emit_line = emit_line
            pending = list(self._buffer)
            self._buffer.clear()
            return pending


def setup_logging() -> logging.Logger:
    """Configure ``sale.*`` → sale.log (+ UI handler). Idempotent."""
    global _configured, _fault_fp, _ui_handler
    log = logging.getLogger(_LOGGER_NAME)
    if _configured:
        return log

    log.setLevel(logging.DEBUG)
    log.propagate = False

    file_handler = logging.FileHandler(
        LOG_PATH, mode="a", encoding="utf-8", delay=False
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        _AsciiFormatter(
            "%(asctime)s.%(msecs)03d %(levelname)-7s "
            "[%(threadName)s] %(name)s.%(funcName)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    log.addHandler(file_handler)

    _ui_handler = UiLogHandler()
    log.addHandler(_ui_handler)

    try:
        _fault_fp = open(LOG_PATH, "a", encoding="utf-8")  # noqa: SIM115
        faulthandler.enable(file=_fault_fp, all_threads=True)
    except OSError:
        faulthandler.enable(all_threads=True)

    _install_excepthooks(log)
    _install_qt_message_handler(log)

    _configured = True
    log.info("=== SALE diagnostics logging started -> %s ===", LOG_PATH)
    log.debug(
        "Python %s | executable %s | cwd %s",
        sys.version.replace("\n", " "),
        sys.executable,
        Path.cwd(),
    )
    log.debug("package dir %s", _PKG_DIR)
    return log


def get_logger(name: str | None = None) -> logging.Logger:
    if not _configured:
        setup_logging()
    if not name:
        return logging.getLogger(_LOGGER_NAME)
    return logging.getLogger(f"{_LOGGER_NAME}.{name}")


def attach_log_view(view: Any) -> None:
    """Mirror sale.* lines into a QPlainTextEdit (Log tab)."""
    if not _configured:
        setup_logging()
    assert _ui_handler is not None

    from PyQt6.QtCore import QObject, pyqtSignal
    from PyQt6.QtGui import QTextCursor

    class _Bridge(QObject):
        line = pyqtSignal(str)

    def _append(text: str) -> None:
        view.appendPlainText(text)
        view.moveCursor(QTextCursor.MoveOperation.End)
        # Cap document size
        doc = view.document()
        if doc.blockCount() > 2000:
            cursor = QTextCursor(doc)
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            cursor.movePosition(
                QTextCursor.MoveOperation.Down,
                QTextCursor.MoveMode.KeepAnchor,
                doc.blockCount() - 2000,
            )
            cursor.removeSelectedText()

    bridge = getattr(view, "_sale_log_bridge", None)
    if bridge is None:
        bridge = _Bridge(view)
        bridge.line.connect(_append)
        view._sale_log_bridge = bridge  # type: ignore[attr-defined]

    # Seed from file tail
    if LOG_PATH.is_file():
        try:
            data = LOG_PATH.read_text(encoding="utf-8", errors="replace")
            lines = data.splitlines()[-500:]
            if lines:
                view.setPlainText("\n".join(lines))
                view.moveCursor(QTextCursor.MoveOperation.End)
        except OSError:
            pass

    pending = _ui_handler.attach(bridge.line.emit)
    for line in pending:
        bridge.line.emit(line)


def log_exception(prefix: str, exc: BaseException | None = None) -> None:
    """Convenience: log full traceback at CRITICAL."""
    log = get_logger("crash")
    if exc is not None:
        log.critical("%s\n%s", prefix, "".join(traceback.format_exception(exc)))
    else:
        log.critical("%s\n%s", prefix, traceback.format_exc())


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

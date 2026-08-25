"""SAGE diagnostics: one file (``sage.log``) + Log tab (same stream).

All ``get_logger(...)`` output goes to both. Lines are tagged with a small set
of categories (``[Texture]``, ``[Text]``, …) for filtering; the Log tab colors
those categories. Call ``attach_log_view`` once the UI Log tab exists.
"""

from __future__ import annotations

import faulthandler
import logging
import re
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Callable

from PyQt6.QtCore import QObject, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QPainter,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextDocument,
)
from PyQt6.QtWidgets import QPlainTextEdit, QWidget

_PKG_DIR = Path(__file__).resolve().parent
LOG_PATH = _PKG_DIR / "sage.log"

_LOGGER_NAME = "sage"
# Log tab keeps only this many lines (file still grows unbounded).
# 999 => line numbers fit in a fixed 3-char gutter.
UI_LOG_MAX_LINES = 999
_UI_LINE_NO_WIDTH = 3
_LINE_NO_COLOR = "#6E6E6E"

# Big buckets — logger leaf name → category label shown as [Label].
_LOGGER_CATEGORY: dict[str, str] = {
    "textures": "Texture",
    "strings": "Text",
    "fonts": "Font",
    "app": "Editor",
    "canvas": "Editor",
    "db_unpack": "System",
}
# Log-tab colors: muted categories; bright levels; white time.
_TIMESTAMP_COLOR = "#FFFFFF"
_LEVEL_COLORS: dict[str, str] = {
    "DEBUG": "#7FDBFF",  # bright cyan
    "INFO": "#7CFF9A",  # bright green
    "WARNING": "#FFD866",  # bright yellow
    "ERROR": "#FF6B6B",  # bright red
    "CRITICAL": "#FF4D94",  # bright magenta-pink
}
_CATEGORY_COLORS: dict[str, str] = {
    "Texture": "#5F8F86",  # muted teal
    "Text": "#8F7A6A",  # muted warm
    "Font": "#8A8A6E",  # muted olive
    "Editor": "#6E8799",  # muted blue-gray
    "System": "#6E6E6E",  # muted gray
}

_configured = False
_fault_fp: Any = None
_ui_handler: "UiLogHandler | None" = None


# Prefer readable ASCII swaps; anything else becomes '?'.
_ASCII_TRANS = str.maketrans(
    {
        "\u2192": "->",  # →
        "\u2190": "<-",  # ←
        "\u00d7": "x",  # ×
        "\u2026": "...",  # …
        "\u2014": "-",  # —
        "\u2013": "-",  # –
        "\u00b7": "|",  # ·
        "\u26a0": "!",  # ⚠
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u00a0": " ",
    }
)


def ascii_only(text: str) -> str:
    """Force log text to ASCII (readable swaps, then '?' for the rest)."""
    if not text:
        return text
    return text.translate(_ASCII_TRANS).encode("ascii", errors="replace").decode("ascii")


def category_for_logger(name: str) -> str:
    """Map ``sage.textures`` / ``textures`` → ``Texture``."""
    short = name
    if short == _LOGGER_NAME:
        return "System"
    prefix = _LOGGER_NAME + "."
    if short.startswith(prefix):
        short = short[len(prefix) :]
    leaf = short.split(".", 1)[0]
    return _LOGGER_CATEGORY.get(leaf, "System")


def _tail_log_lines(path: Path, max_lines: int = UI_LOG_MAX_LINES) -> list[str]:
    """Return the last ``max_lines`` lines of ``path`` (empty if missing)."""
    if max_lines <= 0 or not path.is_file():
        return []
    try:
        with path.open("rb") as fp:
            fp.seek(0, 2)
            size = fp.tell()
            if size <= 0:
                return []
            block = 8192
            data = b""
            pos = size
            while pos > 0 and data.count(b"\n") <= max_lines:
                step = min(block, pos)
                pos -= step
                fp.seek(pos)
                data = fp.read(step) + data
            text = data.decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    return lines


class _CategoryFilter(logging.Filter):
    """Attach ``record.category`` for formatters."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.category = category_for_logger(record.name)  # type: ignore[attr-defined]
        return True


class _UiTabFormatter(logging.Formatter):
    """Compact Log-tab lines: time level [Category] message."""

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "category"):
            record.category = category_for_logger(record.name)  # type: ignore[attr-defined]
        return ascii_only(super().format(record))


class UiLogHandler(logging.Handler):
    """Mirror log records into a QPlainTextEdit (thread-safe via callback)."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.addFilter(_CategoryFilter())
        self.setFormatter(
            _UiTabFormatter(
                "%(asctime)s %(levelname)-5s [%(category)s] %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        self._lock = threading.Lock()
        self._buffer: list[str] = []
        self._emit_line: Callable[[str], None] | None = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:  # noqa: BLE001
            return
        with self._lock:
            sink = self._emit_line
            if sink is None:
                self._buffer.append(msg)
                if len(self._buffer) > UI_LOG_MAX_LINES:
                    self._buffer = self._buffer[-UI_LOG_MAX_LINES:]
                return
        try:
            sink(msg)
        except Exception:  # noqa: BLE001
            pass

    def attach(self, emit_line: Callable[[str], None]) -> list[str]:
        """Bind UI sink; return buffered lines to flush into the view."""
        with self._lock:
            self._emit_line = emit_line
            pending = list(self._buffer)
            self._buffer.clear()
            if len(pending) > UI_LOG_MAX_LINES:
                pending = pending[-UI_LOG_MAX_LINES:]
            return pending


class _FileFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "category"):
            record.category = category_for_logger(record.name)  # type: ignore[attr-defined]
        return ascii_only(super().format(record))


def setup_logging() -> logging.Logger:
    """Configure package logger → ``sage.log`` + Log-tab handler. Idempotent."""
    global _configured, _fault_fp, _ui_handler
    log = logging.getLogger(_LOGGER_NAME)
    if _configured:
        return log

    log.setLevel(logging.DEBUG)
    log.propagate = False

    cat_filter = _CategoryFilter()

    file_handler = logging.FileHandler(LOG_PATH, mode="a", encoding="utf-8", delay=False)
    file_handler.setLevel(logging.DEBUG)
    file_handler.addFilter(cat_filter)
    file_handler.setFormatter(
        _FileFormatter(
            "%(asctime)s.%(msecs)03d %(levelname)-7s "
            "[%(threadName)s] [%(category)s] %(name)s.%(funcName)s:%(lineno)d | %(message)s",
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
    log.info("=== SAGE diagnostics logging started -> %s ===", LOG_PATH)
    log.debug(
        "Python %s | executable %s",
        sys.version.replace("\n", " "),
        sys.executable,
    )
    return log


def get_logger(name: str | None = None) -> logging.Logger:
    """Return ``sage`` or ``sage.<name>`` logger (setup_logging if needed)."""
    if not _configured:
        setup_logging()
    if not name:
        return logging.getLogger(_LOGGER_NAME)
    return logging.getLogger(f"{_LOGGER_NAME}.{name}")


class _LogViewState:
    """Ring buffer of log lines + case-insensitive substring filter for the Log tab.

    UI updates are coalesced (~16ms) so log bursts do not touch the widget once
    per line. Crossing the 999-line cap rebuilds at most once per flush.
    """

    def __init__(self, view: Any) -> None:
        from PyQt6.QtCore import QTimer

        self.view = view
        self.lines: list[str] = []
        self.filter = ""
        # Buffer indices (1-based) for each displayed document block.
        self.display_nums: list[int] = []
        self._pending: list[str] = []
        self._flush_timer = QTimer(view)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(16)
        self._flush_timer.timeout.connect(self._flush)

    def _match(self, line: str) -> bool:
        needle = self.filter
        if not needle:
            return True
        return needle in line.casefold()

    def _scroll_to_end(self) -> None:
        from PyQt6.QtGui import QTextCursor

        self.view.moveCursor(QTextCursor.MoveOperation.End)

    def _visible_pairs(self) -> list[tuple[int, str]]:
        return [
            (i, line)
            for i, line in enumerate(self.lines, start=1)
            if self._match(line)
        ]

    def _gutter_update(self) -> None:
        bar = getattr(self.view, "_sage_log_line_bar", None)
        if bar is not None:
            bar.update()

    def _apply_view(self) -> None:
        """Push ``self.lines`` (respecting filter) into the widget once."""
        pairs = self._visible_pairs()
        self.display_nums = [n for n, _ in pairs]
        view = self.view
        view.setUpdatesEnabled(False)
        try:
            view.setPlainText("\n".join(t for _, t in pairs))
            self._scroll_to_end()
        finally:
            view.setUpdatesEnabled(True)
        self._gutter_update()

    def _flush(self) -> None:
        pending = self._pending
        if not pending:
            return
        self._pending = []
        old_len = len(self.lines)
        self.lines.extend(pending)
        trimmed = len(self.lines) > UI_LOG_MAX_LINES
        if trimmed:
            self.lines = self.lines[-UI_LOG_MAX_LINES:]

        # Filter or ring wrap: one rebuild. Otherwise append the whole burst.
        if self.filter or trimmed:
            self._apply_view()
            return

        self.display_nums.extend(range(old_len + 1, old_len + 1 + len(pending)))
        view = self.view
        view.setUpdatesEnabled(False)
        try:
            view.appendPlainText("\n".join(pending))
            self._scroll_to_end()
        finally:
            view.setUpdatesEnabled(True)
        self._gutter_update()

    def _flush_now(self) -> None:
        if self._flush_timer.isActive():
            self._flush_timer.stop()
        if self._pending:
            self._flush()

    def append(self, text: str) -> None:
        self._pending.append(text)
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def set_filter(self, text: str) -> None:
        needle = (text or "").casefold()
        self._flush_now()
        if needle == self.filter:
            return
        self.filter = needle
        self._apply_view()

    def replace_lines(self, lines: list[str]) -> None:
        if self._flush_timer.isActive():
            self._flush_timer.stop()
        self._pending.clear()
        self.lines = list(lines[-UI_LOG_MAX_LINES:])
        self._apply_view()


def set_log_filter(view: Any, text: str) -> None:
    """Apply a case-insensitive substring filter to a Log tab view."""
    state = getattr(view, "_sage_log_state", None)
    if state is None:
        return
    state.set_filter(text)


class _LogLineNumberBar(QWidget):
    """Fixed sidebar gutter; wrapped text stays in the editor viewport."""

    def __init__(self, editor: "LogTextEdit") -> None:
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._editor.log_line_number_area_width(), 0)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor("#252526"))
        state = getattr(self._editor, "_sage_log_state", None)
        nums: list[int] = state.display_nums if state is not None else []

        block = self._editor.firstVisibleBlock()
        block_number = block.blockNumber()
        geo = self._editor.blockBoundingGeometry(block).translated(
            self._editor.contentOffset()
        )
        top = int(geo.top())
        bottom = top + int(self._editor.blockBoundingRect(block).height())
        fm_h = self._editor.fontMetrics().height()
        painter.setPen(QColor(_LINE_NO_COLOR))

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                if 0 <= block_number < len(nums):
                    label = f"{nums[block_number]:{_UI_LINE_NO_WIDTH}d}"
                else:
                    label = f"{block_number + 1:{_UI_LINE_NO_WIDTH}d}"
                painter.drawText(
                    0,
                    top,
                    self.width() - 6,
                    fm_h,
                    int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop),
                    label,
                )
            block = block.next()
            block_number += 1
            top = bottom
            bottom = top + int(self._editor.blockBoundingRect(block).height())

        painter.setPen(QColor("#3C3C3C"))
        x = self.width() - 1
        painter.drawLine(x, event.rect().top(), x, event.rect().bottom())


class LogTextEdit(QPlainTextEdit):
    """Read-only log view with a dedicated line-number sidebar."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sage_log_line_bar = _LogLineNumberBar(self)
        # Width is fixed (3 digits) — do not re-layout on every blockCountChanged.
        self.updateRequest.connect(self._on_log_update_request)
        self._apply_log_line_number_area_width()

    def log_line_number_area_width(self) -> int:
        digit_w = self.fontMetrics().horizontalAdvance("9")
        return 6 + digit_w * _UI_LINE_NO_WIDTH + 6

    def _apply_log_line_number_area_width(self) -> None:
        w = self.log_line_number_area_width()
        self.setViewportMargins(w, 0, 0, 0)
        self._sync_log_line_number_bar_geometry()

    def _sync_log_line_number_bar_geometry(self) -> None:
        cr = self.contentsRect()
        w = self.log_line_number_area_width()
        self._sage_log_line_bar.setGeometry(QRect(cr.left(), cr.top(), w, cr.height()))

    def _on_log_update_request(self, rect: QRect, dy: int) -> None:
        bar = self._sage_log_line_bar
        if dy:
            bar.scroll(0, dy)
        else:
            bar.update(0, rect.y(), bar.width(), rect.height())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_log_line_number_bar_geometry()


def attach_log_view(view: Any) -> None:
    """Wire the Log tab so every sage.* record appears there (and in sage.log).

    Seeds from the last ``UI_LOG_MAX_LINES`` of ``sage.log``, caps the widget,
    and colorizes time (white), level (bright), and ``[Category]`` (muted).
    Use ``LogTextEdit`` for a sidebar line-number gutter; ``set_log_filter`` to filter.
    """
    if not _configured:
        setup_logging()
    assert _ui_handler is not None

    class _Bridge(QObject):
        line = pyqtSignal(str)

    def _char_fmt(hex_color: str) -> QTextCharFormat:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(hex_color))
        return fmt

    class _LogCategoryHighlighter(QSyntaxHighlighter):
        # UI: HH:MM:SS  |  file seed: YYYY-MM-DD HH:MM:SS.mmm
        _TS_RE = re.compile(
            r"^(?:"
            r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3}"
            r"|"
            r"\d{2}:\d{2}:\d{2}"
            r")"
        )
        _LEVEL_RE = re.compile(
            r"\b(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b"
        )

        def __init__(self, document: QTextDocument) -> None:
            super().__init__(document)
            self._ts_fmt = _char_fmt(_TIMESTAMP_COLOR)
            self._level_fmts = {
                name: _char_fmt(color) for name, color in _LEVEL_COLORS.items()
            }
            self._cat_fmts: list[tuple[str, QTextCharFormat]] = [
                (f"[{cat}]", _char_fmt(color))
                for cat, color in _CATEGORY_COLORS.items()
            ]

        def highlightBlock(self, text: str) -> None:  # noqa: N802
            if not text:
                return
            m = self._TS_RE.match(text)
            if m:
                self.setFormat(0, m.end(), self._ts_fmt)
            for m in self._LEVEL_RE.finditer(text):
                fmt = self._level_fmts.get(m.group(1))
                if fmt is not None:
                    self.setFormat(m.start(), m.end() - m.start(), fmt)
            for cat_token, cat_fmt in self._cat_fmts:
                idx = text.find(cat_token)
                if idx >= 0:
                    self.setFormat(idx, len(cat_token), cat_fmt)

    state = getattr(view, "_sage_log_state", None)
    if state is None:
        state = _LogViewState(view)
        view._sage_log_state = state  # type: ignore[attr-defined]

    bridge = getattr(view, "_sage_log_bridge", None)
    if bridge is None:
        bridge = _Bridge(view)
        bridge.line.connect(state.append)
        view._sage_log_bridge = bridge  # type: ignore[attr-defined]

    if getattr(view, "_sage_log_highlighter", None) is None:
        view._sage_log_highlighter = _LogCategoryHighlighter(view.document())  # type: ignore[attr-defined]

    seed = _tail_log_lines(LOG_PATH, UI_LOG_MAX_LINES)
    pending = _ui_handler.attach(bridge.line.emit)
    if seed:
        state.replace_lines(seed)
    else:
        state.replace_lines([])
        for line in pending:
            bridge.line.emit(line)


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

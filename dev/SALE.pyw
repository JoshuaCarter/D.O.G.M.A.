"""SALE — Stalker Anomaly Loadout Editor (double-click launcher, no console)."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_DEV = Path(__file__).resolve().parent
if str(_DEV) not in sys.path:
    sys.path.insert(0, str(_DEV))


def _fail(exc: BaseException) -> int:
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        from sale.diaglog import get_logger, setup_logging

        setup_logging()
        get_logger("launch").critical("SALE failed to start\n%s", text)
    except Exception:
        pass
    try:
        from PyQt6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(None, "SALE failed to start", text)
    except Exception:
        log = Path(__file__).resolve().parent / "sale" / "sale_launch_error.log"
        try:
            log.write_text(text, encoding="utf-8")
        except OSError:
            pass
    return 1


try:
    from sale.diaglog import setup_logging

    setup_logging()
    from sale.app import main
except Exception as exc:  # noqa: BLE001
    raise SystemExit(_fail(exc)) from exc

try:
    raise SystemExit(main())
except SystemExit:
    raise
except Exception as exc:  # noqa: BLE001
    raise SystemExit(_fail(exc)) from exc

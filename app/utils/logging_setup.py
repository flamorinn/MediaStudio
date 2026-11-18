"""Dosya logu (ayrıntılı) + konsol logu (yalnızca uyarı ve üstü) + Qt mesajlarının yönlendirilmesi."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.utils.paths import logs_dir

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def setup_logging(level: int = logging.INFO) -> Path:
    log_file = logs_dir() / "mediastudio.log"
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    file_handler = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(file_handler)

    if sys.stderr is not None:  # pencereli EXE'de konsol yoktur
        console = logging.StreamHandler()
        console.setLevel(logging.WARNING)
        console.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(console)
    return log_file


def install_qt_message_handler() -> None:
    """Qt'nin kendi uyarılarını konsola basmak yerine log dosyasına yönlendirir."""
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler

    qt_log = logging.getLogger("qt")
    levels = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.INFO,  # Qt uyarıları çoğunlukla zararsız; dosyaya yazılır
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }

    def handler(mode, _context, message):
        qt_log.log(levels.get(mode, logging.INFO), message)

    qInstallMessageHandler(handler)

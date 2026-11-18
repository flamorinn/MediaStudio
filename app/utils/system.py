"""İşletim sistemi işlemleri: gizli alt süreç, klasör açma, Gezgin'de gösterme."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_hidden(args: list[str], timeout: float = 15) -> subprocess.CompletedProcess:
    """Konsol penceresi açmadan alt süreç çalıştırır (pencereli EXE için gerekli)."""
    return subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, creationflags=_NO_WINDOW,
    )


def open_folder(path: str | Path) -> bool:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    folder = Path(path)
    if not folder.is_dir():
        return False
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def reveal_in_explorer(path: str | Path) -> bool:
    target = Path(path)
    if not target.exists():
        return False
    if os.name == "nt":
        subprocess.Popen(["explorer", "/select,", str(target)], creationflags=_NO_WINDOW)  # noqa: S603,S607
        return True
    return open_folder(target.parent)


def is_writable_dir(path: str | Path) -> tuple[bool, str]:
    """Klasörü oluşturup içine deneme dosyası yazarak yazılabilirliği doğrular."""
    target = Path(path)
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".mediastudio_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return False, str(exc)
    return True, ""

"""Yol çözümü. Kaynak koddan ve PyInstaller tek dosya EXE'den çalışırken ortaktır."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from app import APP_ID, APP_NAME

DATA_DIR_ENV = "MEDIASTUDIO_DATA_DIR"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def project_root() -> Path:
    """Paketlenmiş kaynakların kökü (EXE'de _MEIPASS, geliştirmede proje klasörü)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[2]


def exe_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return project_root()


def asset_path(*parts: str) -> Path:
    return project_root().joinpath("assets", *parts)


def user_data_dir() -> Path:
    """Yazılabilir veri klasörü: %APPDATA%\\MediaStudio (testlerde ortam değişkeniyle değişir)."""
    override = os.environ.get(DATA_DIR_ENV)
    base = Path(override) if override else Path(os.environ.get("APPDATA") or Path.home()) / APP_ID
    base.mkdir(parents=True, exist_ok=True)
    return base


def logs_dir() -> Path:
    path = user_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_download_dir() -> Path:
    """Geliştirmede proje içindeki downloads/, EXE'de kullanıcının İndirilenler klasörü."""
    if is_frozen():
        return Path.home() / "Downloads" / APP_NAME
    return project_root() / "downloads"

"""JSON tabanlı kalıcı ayarlar (%APPDATA%\\MediaStudio\\settings.json)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, fields
from pathlib import Path

from app.core.errors import SettingsError
from app.models.settings import AppSettings
from app.utils.paths import default_download_dir, user_data_dir

log = logging.getLogger(__name__)

_FIELD_NAMES = {f.name for f in fields(AppSettings)}


class SettingsManager:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (user_data_dir() / "settings.json")
        self.recovered_from_corruption = False
        self.settings = self._load()

    # ------------------------------------------------------------------ okuma/yazma
    def _load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings().normalized()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("kök öğe bir nesne değil")
            return AppSettings(**{k: v for k, v in raw.items() if k in _FIELD_NAMES}).normalized()
        except (ValueError, TypeError, OSError) as exc:
            backup = self.path.with_name("settings.corrupt.json")
            log.warning("Ayar dosyası okunamadı (%s); varsayılanlar kullanılıyor. Yedek: %s", exc, backup)
            try:
                os.replace(self.path, backup)
            except OSError:
                pass
            self.recovered_from_corruption = True
            return AppSettings().normalized()

    def save(self) -> None:
        self.settings.normalized()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(self.settings), indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)  # atomik: yarıda kalan yazma eski dosyayı bozmaz
        except OSError as exc:
            raise SettingsError("Ayarlar kaydedilemedi.", detail=str(exc)) from exc

    # ------------------------------------------------------------------ API
    def update(self, **changes) -> list[str]:
        """Değişiklikleri uygular, kaydeder ve gerçekten değişen alanların adlarını döndürür."""
        unknown = set(changes) - _FIELD_NAMES
        if unknown:
            raise KeyError(f"Bilinmeyen ayar: {', '.join(sorted(unknown))}")
        changed = [k for k, v in changes.items() if getattr(self.settings, k) != v]
        if not changed:
            return []
        for key in changed:
            setattr(self.settings, key, changes[key])
        self.save()
        return changed

    def reset(self, keep: tuple[str, ...] = ("window_geometry",)) -> None:
        fresh = AppSettings().normalized()
        for key in keep:
            setattr(fresh, key, getattr(self.settings, key))
        self.settings = fresh
        self.save()

    def download_dir(self) -> Path:
        return Path(self.settings.download_dir) if self.settings.download_dir else default_download_dir()

    def ensure_download_dir(self) -> Path:
        path = self.download_dir()
        path.mkdir(parents=True, exist_ok=True)
        return path

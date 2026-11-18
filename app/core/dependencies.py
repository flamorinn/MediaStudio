"""Harici bağımlılıkların (FFmpeg, FFprobe, yt-dlp) bulunması ve doğrulanması.

FFmpeg arama sırası:
    1. Ayarlar'da kullanıcının seçtiği yol (dosya veya klasör)
    2. Uygulamayla paketlenmiş kopya (EXE içi `bin/` veya EXE yanındaki `bin/`)
    3. Sistem PATH
    4. Bilinen kurulum konumları (winget, Chocolatey, Scoop, C:\\ffmpeg ...)
FFprobe önce FFmpeg ile aynı klasörde, sonra PATH'te aranır.
Her aday `-version` ile gerçekten çalıştırılarak doğrulanır.
"""

from __future__ import annotations

import glob
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from app.utils.paths import exe_dir, project_root
from app.utils.system import run_hidden

log = logging.getLogger(__name__)

IS_WINDOWS = os.name == "nt"
FFMPEG_EXE = "ffmpeg.exe" if IS_WINDOWS else "ffmpeg"
FFPROBE_EXE = "ffprobe.exe" if IS_WINDOWS else "ffprobe"
_VERSION_RE = re.compile(r"(?:ffmpeg|ffprobe) version (\S+)")

SOURCE_LABELS = {
    "custom": "Kullanıcı seçimi",
    "bundled": "Uygulamayla birlikte",
    "path": "Sistem PATH",
    "system": "Bilinen kurulum konumu",
    "python": "Python paketi",
}


@dataclass(frozen=True)
class ToolInfo:
    name: str
    path: Path | None
    version: str
    source: str

    @property
    def short_version(self) -> str:
        return self.version.split("-")[0]


@dataclass
class DependencyReport:
    ffmpeg: ToolInfo | None = None
    ffprobe: ToolInfo | None = None
    ytdlp: ToolInfo | None = None
    custom_path_rejected: bool = False
    tried: list[str] = field(default_factory=list)

    @property
    def media_ready(self) -> bool:
        """Dönüştürme/kesme/birleştirme için FFmpeg ve FFprobe hazır mı?"""
        return self.ffmpeg is not None and self.ffprobe is not None

    @property
    def all_ready(self) -> bool:
        return self.media_ready and self.ytdlp is not None


class DependencyChecker:
    # ------------------------------------------------------------------ yardımcılar
    def probe_version(self, executable: Path) -> str | None:
        """Dosya gerçekten çalışan bir ffmpeg/ffprobe ise sürümünü döndürür."""
        if not executable.is_file():
            return None
        try:
            proc = run_hidden([str(executable), "-hide_banner", "-version"], timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("Sürüm sorgusu başarısız %s: %s", executable, exc)
            return None
        if proc.returncode != 0:
            return None
        match = _VERSION_RE.search(proc.stdout)
        return match.group(1) if match else None

    @staticmethod
    def normalize_custom(custom: str) -> Path:
        path = Path(custom).expanduser()
        if path.is_dir():
            return path / "bin" / FFMPEG_EXE if (path / "bin" / FFMPEG_EXE).is_file() else path / FFMPEG_EXE
        return path

    def _automatic_candidates(self) -> list[tuple[str, Path]]:
        found: list[tuple[str, Path]] = [
            ("bundled", project_root() / "bin" / FFMPEG_EXE),
            ("bundled", exe_dir() / "bin" / FFMPEG_EXE),
            ("bundled", exe_dir() / FFMPEG_EXE),
        ]
        on_path = shutil.which("ffmpeg")
        if on_path:
            found.append(("path", Path(on_path)))
        if IS_WINDOWS:
            local = os.environ.get("LOCALAPPDATA", "")
            program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
            patterns = [
                os.path.join(local, "Microsoft", "WinGet", "Links", FFMPEG_EXE),
                os.path.join(local, "Microsoft", "WinGet", "Packages", "*FFmpeg*", "*", "bin", FFMPEG_EXE),
                os.path.join(program_files, "ffmpeg", "bin", FFMPEG_EXE),
                r"C:\ffmpeg\bin\ffmpeg.exe",
                r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
                os.path.join(str(Path.home()), "scoop", "shims", FFMPEG_EXE),
            ]
            for pattern in patterns:
                for match in sorted(glob.glob(pattern), reverse=True):
                    found.append(("system", Path(match)))
        return found

    def _find_ffprobe(self, ffmpeg: ToolInfo) -> ToolInfo | None:
        candidates = [(ffmpeg.source, ffmpeg.path.with_name(FFPROBE_EXE))]
        on_path = shutil.which("ffprobe")
        if on_path:
            candidates.append(("path", Path(on_path)))
        for source, path in candidates:
            version = self.probe_version(path)
            if version:
                return ToolInfo("FFprobe", path.resolve(), version, source)
        return None

    @staticmethod
    def detect_ytdlp() -> ToolInfo | None:
        try:
            from yt_dlp.version import __version__
        except Exception as exc:  # noqa: BLE001 - paket eksik veya bozuk olabilir
            log.warning("yt-dlp yüklenemedi: %s", exc)
            return None
        return ToolInfo("yt-dlp", None, __version__, "python")

    # ------------------------------------------------------------------ API
    def detect(self, custom_ffmpeg: str | None = None) -> DependencyReport:
        report = DependencyReport()
        candidates: list[tuple[str, Path]] = []
        if custom_ffmpeg:
            candidates.append(("custom", self.normalize_custom(custom_ffmpeg)))
        candidates.extend(self._automatic_candidates())

        seen: set[str] = set()
        for source, path in candidates:
            key = os.path.normcase(str(path))
            if key in seen:
                continue
            seen.add(key)
            report.tried.append(str(path))
            version = self.probe_version(path)
            if version:
                report.ffmpeg = ToolInfo("FFmpeg", path.resolve(), version, source)
                break
            if source == "custom":
                report.custom_path_rejected = True
                log.warning("Seçilen FFmpeg yolu geçersiz: %s", path)

        if report.ffmpeg:
            report.ffprobe = self._find_ffprobe(report.ffmpeg)
            log.info("FFmpeg: %s (%s) | FFprobe: %s", report.ffmpeg.path, report.ffmpeg.version,
                     report.ffprobe.path if report.ffprobe else "yok")
        else:
            log.warning("FFmpeg bulunamadı. Denenen yollar: %s", report.tried)
        report.ytdlp = self.detect_ytdlp()
        return report

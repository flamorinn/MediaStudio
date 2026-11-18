"""Testler için yerel medya sunucusu (internete çıkılmaz).

FFmpeg ile üretilen sentetik test içeriklerini 127.0.0.1 üzerinden sunar:
  /sample.mp4            görüntü + ses içeren MP4 (doğrudan dosya)
  /dash/manifest.mpd     ayrı görüntü ve ses akışlı DASH (birleştirme testi)
  /slow/big.mp4          yavaş akan büyük dosya (iptal ve eşzamanlılık testleri)
  /yok.mp4               404
"""

from __future__ import annotations

import functools
import os
import http.server
import shutil
import subprocess
import threading
import time
from pathlib import Path

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _ffmpeg(ffmpeg: str, args: list[str], cwd: Path | None = None) -> None:
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args], check=True, cwd=cwd,
                   creationflags=NO_WINDOW, capture_output=True)


def build_media(root: Path, ffmpeg: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    sample = root / "sample.mp4"
    if not sample.exists():
        _ffmpeg(ffmpeg, ["-f", "lavfi", "-i", "testsrc=size=640x360:rate=25", "-f", "lavfi",
                         "-i", "sine=frequency=440:sample_rate=44100", "-t", "6", "-c:v", "libx264",
                         "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                         "-shortest", str(sample)])
    dash = root / "dash"
    if not (dash / "manifest.mpd").exists():
        dash.mkdir(exist_ok=True)
        # DASH segmentleri çalışma dizinine göre yazılır; bu yüzden cwd = dash
        _ffmpeg(ffmpeg, ["-i", str(sample), "-map", "0:v", "-map", "0:a", "-c", "copy", "-f", "dash",
                         "-seg_duration", "2", "-adaptation_sets", "id=0,streams=v id=1,streams=a",
                         "manifest.mpd"], cwd=dash)
    slow = root / "slow"
    slow.mkdir(exist_ok=True)
    if not (slow / "big.mp4").exists():
        shutil.copy2(sample, slow / "big.mp4")
        with open(slow / "big.mp4", "ab") as fh:  # ~4 MB: yavaş akışta birkaç saniye sürer
            fh.write(b"\0" * (4 * 1024 * 1024))
    return root


class _Handler(http.server.SimpleHTTPRequestHandler):
    slow_delay = 0.05
    slow_chunk = 64 * 1024

    def log_message(self, *args) -> None:  # test çıktısını kirletmesin
        pass

    # HTTP Range desteği: gerçek medya sunucuları (YouTube dahil) bayt aralığı isteklerini destekler;
    # FFmpeg zaman aralığı indirirken dosyanın ortasına bu yolla atlar.
    _range: tuple[int, int] | None = None

    def send_head(self):
        self._range = None
        header = self.headers.get("Range", "")
        path = self.translate_path(self.path)
        if not header.startswith("bytes=") or not os.path.isfile(path):
            return super().send_head()
        size = os.path.getsize(path)
        first, _, last = header[6:].split(",")[0].partition("-")
        try:
            start = int(first) if first else max(size - int(last), 0)
            end = int(last) if first and last else size - 1
        except ValueError:
            return super().send_head()
        if start >= size:
            self.send_error(416, "Requested Range Not Satisfiable")
            return None
        end = min(end, size - 1)
        source = open(path, "rb")  # noqa: SIM115 - copyfile kapatır
        source.seek(start)
        self._range = (start, end)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        return source

    def end_headers(self) -> None:
        if self._range is None:
            self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def copyfile(self, source, outputfile) -> None:
        remaining = (self._range[1] - self._range[0] + 1) if self._range else None
        slow = self.path.startswith("/slow/")
        chunk_size = self.slow_chunk if slow else 256 * 1024
        while True:
            want = chunk_size if remaining is None else min(chunk_size, remaining)
            if want <= 0:
                return
            chunk = source.read(want)
            if not chunk:
                return
            try:
                outputfile.write(chunk)
                outputfile.flush()
            except (ConnectionError, OSError):
                return
            if remaining is not None:
                remaining -= len(chunk)
            if slow:
                time.sleep(self.slow_delay)


class MediaServer:
    def __init__(self, root: Path) -> None:
        handler = functools.partial(_Handler, directory=str(root))
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def url(self, path: str) -> str:
        return f"{self.base}/{path.lstrip('/')}"

    def __enter__(self) -> "MediaServer":
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

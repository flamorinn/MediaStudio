"""Testler için yerel medya sunucusu (internete çıkılmaz).

FFmpeg ile üretilen sentetik test içeriklerini 127.0.0.1 üzerinden sunar:
  /sample.mp4            görüntü + ses içeren MP4 (doğrudan dosya)
  /dash/manifest.mpd     ayrı görüntü ve ses akışlı DASH (birleştirme testi)
  /slow/big.mp4          yavaş akan büyük dosya (iptal ve eşzamanlılık testleri)
  /yok.mp4               404
"""

from __future__ import annotations

import functools
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

    def copyfile(self, source, outputfile) -> None:
        if not self.path.startswith("/slow/"):
            return super().copyfile(source, outputfile)
        while chunk := source.read(self.slow_chunk):
            try:
                outputfile.write(chunk)
                outputfile.flush()
            except (ConnectionError, OSError):
                return
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

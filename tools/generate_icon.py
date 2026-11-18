"""Uygulama logosunu (app/ui/theme/icons.py: LOGO_SVG) çok boyutlu Windows .ico dosyasına çevirir.

Qt .ico yazamadığı için ICO kapsayıcısı elle yazılır (Windows Vista+ PNG gömülü ICO destekler).
Kullanım:  python tools/generate_icon.py
Çıktı:     assets/icons/app.ico  ve  assets/logo.svg
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

from app.ui.theme.icons import LOGO_SVG  # noqa: E402
from app.ui.theme.tokens import ACCENT_PRESETS  # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)


def render_png(svg: str, size: int) -> bytes:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(painter, QRectF(0, 0, size, size))
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def write_ico(path: Path, images: list[tuple[int, bytes]]) -> None:
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in images:
        dim = 0 if size >= 256 else size  # 0 = 256 piksel
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset + len(blobs))
        blobs += data
    path.write_bytes(header + entries + blobs)


def main() -> None:
    _app = QGuiApplication.instance() or QGuiApplication([])
    preset = ACCENT_PRESETS["aurora"]
    svg = LOGO_SVG.format(c1=preset["primary"], c2=preset["secondary"])
    (ROOT / "assets" / "icons").mkdir(parents=True, exist_ok=True)
    (ROOT / "assets" / "logo.svg").write_text(svg, encoding="utf-8")
    ico = ROOT / "assets" / "icons" / "app.ico"
    write_ico(ico, [(s, render_png(svg, s)) for s in SIZES])
    print(f"Yazıldı: {ico} ({ico.stat().st_size} bayt, {len(SIZES)} boyut)")


if __name__ == "__main__":
    main()

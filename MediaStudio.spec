# -*- mode: python ; coding: utf-8 -*-
# Tek dosya Windows EXE:   pyinstaller --noconfirm --clean MediaStudio.spec
#
# FFmpeg'i EXE'ye gömmek için vendor/ffmpeg/ klasörüne ffmpeg.exe ve ffprobe.exe koyun
# (EXE içinde bin/ altına yerleşir ve otomatik algılamada önceliklidir). Koymazsanız
# uygulama çalışırken FFmpeg'i PATH, winget, Chocolatey, Scoop ve C:\ffmpeg altında arar.

from pathlib import Path

vendor = Path("vendor/ffmpeg")
binaries = [(str(p), "bin") for p in (vendor / "ffmpeg.exe", vendor / "ffprobe.exe") if p.is_file()]

datas = [("assets/licenses", "assets/licenses"), ("assets/logo.svg", "assets")]
icon = "assets/icons/app.ico" if Path("assets/icons/app.ico").is_file() else None

# Kullanılmayan Qt modüllerini dışarıda bırakarak EXE boyutunu küçült
excluded = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtQuickWidgets",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtBluetooth",
    "PySide6.QtSql", "PySide6.QtDesigner", "PySide6.QtOpenGL", "PySide6.QtNetwork",
    "tkinter", "pytest", "pytestqt", "unittest",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=["PySide6.QtSvg"],
    excludes=excluded,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="MediaStudio",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=icon,
)

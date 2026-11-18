"""Dosya adı yardımcıları (servislerden bağımsız)."""

from __future__ import annotations

import os
from pathlib import Path

TEMP_MARKER = ".converting"


def same_path(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def unique_output(directory: Path, stem: str, ext: str, reserved: set[str] | None = None) -> Path:
    """Var olmayan, geçici dosyası da olmayan ve başka bir işe ayrılmamış bir çıktı yolu döndürür.

    Çakışmada 'Ad (2).mp3', 'Ad (3).mp3' ... biçiminde numaralandırır.
    """
    reserved = {os.path.normcase(r) for r in (reserved or set())}
    base, n = stem, 2
    while True:
        candidate = directory / f"{stem}.{ext}"
        temp = directory / f"{stem}{TEMP_MARKER}.{ext}"
        if not candidate.exists() and not temp.exists() and os.path.normcase(str(candidate)) not in reserved:
            return candidate
        stem = f"{base} ({n})"
        n += 1


def temp_path_for(output: Path) -> Path:
    return output.with_name(f"{output.stem}{TEMP_MARKER}{output.suffix}")

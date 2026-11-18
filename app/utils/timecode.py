"""Zaman kodu ayrıştırma ve biçimlendirme (ss:dd:sn.ms)."""

from __future__ import annotations

import re

_TC_RE = re.compile(r"^\s*(?:(\d+):)?(?:(\d+):)?(\d+(?:[.,]\d+)?)\s*$")


def parse_timecode(text: str) -> float:
    """'1:02:03.5', '02:03', '83,25' veya '83' → saniye. Geçersizse ValueError.

    Dakika ve saniye alanları, üstlerinde bir alan varsa 60'tan küçük olmalıdır.
    """
    match = _TC_RE.match(text or "")
    if not match:
        raise ValueError("Geçersiz zaman. Örnek: 01:23 veya 00:01:23.500")
    a, b, c = match.groups()
    seconds = float(c.replace(",", "."))
    if a is not None and b is not None:          # ss:dd:sn
        hours, minutes = int(a), int(b)
    elif a is not None:                          # dd:sn
        hours, minutes = 0, int(a)
    else:                                        # sn
        hours, minutes = 0, 0
    if (a is not None) and seconds >= 60:
        raise ValueError("Saniye 60'tan küçük olmalı.")
    if a is not None and b is not None and minutes >= 60:
        raise ValueError("Dakika 60'tan küçük olmalı.")
    return hours * 3600 + minutes * 60 + seconds


def format_timecode(seconds: float, millis: bool = True) -> str:
    """Saniye → '00:01:23.500' (millis=False ise '00:01:23')."""
    total_ms = int(round(max(seconds, 0) * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}" if millis else f"{h:02d}:{m:02d}:{s:02d}"


def filename_timecode(seconds: float) -> str:
    """Dosya adında kullanılabilir biçim (Windows ':' kabul etmez): '00.01.23'."""
    return format_timecode(seconds, millis=False).replace(":", ".")

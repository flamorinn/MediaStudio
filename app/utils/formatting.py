"""İnsan tarafından okunabilir biçimlendirme (Türkçe)."""

from __future__ import annotations

from datetime import datetime

_UNITS = ("B", "KB", "MB", "GB", "TB")
_MONTHS = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos",
           "Eylül", "Ekim", "Kasım", "Aralık")
_DAYS = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")


def human_size(num_bytes: float) -> str:
    size = float(max(num_bytes, 0))
    for unit in _UNITS:
        if size < 1024 or unit == _UNITS[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}".replace(".", ",")
        size /= 1024
    return f"{size:.1f} TB"


def human_speed(bps: float) -> str:
    return f"{human_size(bps)}/sn"


def human_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "—"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def relative_time(iso: str, now: datetime | None = None) -> str:
    try:
        when = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    now = now or datetime.now()
    delta = (now - when).total_seconds()
    if delta < 60:
        return "az önce"
    if delta < 3600:
        return f"{int(delta // 60)} dk önce"
    if delta < 86400 and now.date() == when.date():
        return f"{int(delta // 3600)} sa önce"
    if (now.date() - when.date()).days == 1:
        return f"dün {when:%H:%M}"
    return when.strftime("%d.%m.%Y %H:%M")


def long_date(when: datetime | None = None) -> str:
    when = when or datetime.now()
    return f"{when.day} {_MONTHS[when.month - 1]} {when.year}, {_DAYS[when.weekday()]}"


def greeting(when: datetime | None = None) -> str:
    hour = (when or datetime.now()).hour
    if 5 <= hour < 12:
        return "Günaydın"
    if 12 <= hour < 18:
        return "İyi günler"
    return "İyi akşamlar"

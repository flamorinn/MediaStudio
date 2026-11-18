"""Kullanıcıya gösterilen bildirim."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class NotificationLevel(str, Enum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"

    @property
    def icon(self) -> str:
        return {"info": "info", "success": "check-circle", "warning": "alert-triangle", "error": "x-circle"}[self.value]

    @property
    def timeout_ms(self) -> int:
        return {"info": 4500, "success": 4000, "warning": 6500, "error": 9000}[self.value]


@dataclass(frozen=True)
class Notification:
    level: NotificationLevel
    title: str
    message: str = ""
    details: str = ""  # doluysa "Ayrıntılar" düğmesi gösterilir (ör. traceback)

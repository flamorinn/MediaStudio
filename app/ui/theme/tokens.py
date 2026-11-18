"""Tasarım belirteçleri (design tokens): renkler, boşluklar, yarıçaplar, tipografi."""

from __future__ import annotations

from dataclasses import dataclass

NEUTRALS = {
    "window": "#0a0b0f",        # en dış katman
    "sidebar": "#0c0d12",
    "bg": "#101117",            # antrasit içerik zemini
    "surface": "#15161d",       # kart
    "surface_2": "#1b1c25",     # kart içi alan / giriş
    "surface_hover": "#20222c",
    "border": "#23252f",
    "border_strong": "#2e313d",
    "text": "#ececf1",
    "text_2": "#a3a6b4",
    "text_3": "#6b6f7d",
    "success": "#34d399",
    "warning": "#fbbf24",
    "danger": "#fb7185",
    "info": "#60a5fa",
}

ACCENT_PRESETS = {
    "aurora": {"primary": "#8b5cf6", "primary_hover": "#a07bff", "secondary": "#3d8bff"},
    "electric": {"primary": "#3d8bff", "primary_hover": "#62a2ff", "secondary": "#22d3ee"},
    "violet": {"primary": "#a855f7", "primary_hover": "#bb7bfa", "secondary": "#7c3aed"},
}

SPACING = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}
RADIUS = {"sm": 8, "md": 10, "lg": 14, "xl": 18}

FONT_FAMILIES = ["Segoe UI Variable Text", "Segoe UI", "Inter", "Arial"]
DISPLAY_FAMILIES = ["Segoe UI Variable Display", "Segoe UI Semibold", "Segoe UI", "Arial"]


def rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {int(alpha * 255)})"


@dataclass(frozen=True)
class Palette:
    accent: str
    colors: dict

    def __getitem__(self, key: str) -> str:
        return self.colors[key]

    def tone(self, tone: str) -> str:
        """Ton adını renge çevirir: accent | secondary | success | warning | danger | info | neutral."""
        return {
            "accent": self.colors["primary"],
            "secondary": self.colors["secondary"],
            "neutral": self.colors["text_2"],
        }.get(tone, self.colors.get(tone, self.colors["text_2"]))


def build_palette(accent: str) -> Palette:
    preset = ACCENT_PRESETS.get(accent, ACCENT_PRESETS["aurora"])
    return Palette(accent if accent in ACCENT_PRESETS else "aurora", {**NEUTRALS, **preset})

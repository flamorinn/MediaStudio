"""Paletten Qt stil sayfası (QSS) üretir. Tüm görsel kurallar burada toplanır."""

from __future__ import annotations

from app.ui.theme.tokens import RADIUS, Palette, rgba


def build_stylesheet(p: Palette) -> str:
    c = p.colors
    grad = f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {c['primary']}, stop:1 {c['secondary']})"
    grad_hover = f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {c['primary_hover']}, stop:1 {c['secondary']})"
    r = RADIUS
    return f"""
* {{
    font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif;
    font-size: 10pt;
    color: {c['text']};
    outline: none;
}}
QMainWindow, QWidget#Root {{ background: {c['window']}; }}
QWidget#Content, QWidget#Page, QWidget#PageBody, QScrollArea#PageScroll,
QScrollArea#PageScroll > QWidget > QWidget {{ background: {c['bg']}; }}
QScrollArea {{ border: none; background: transparent; }}
QToolTip {{ background: {c['surface_2']}; color: {c['text']}; border: 1px solid {c['border_strong']};
           padding: 6px 8px; border-radius: 6px; }}

/* ------------------------------------------------------------- sidebar */
QFrame#Sidebar {{ background: {c['sidebar']}; border-right: 1px solid {c['border']}; }}
QLabel#BrandName {{ font-family: "Segoe UI Variable Display", "Segoe UI"; font-size: 13.5pt; font-weight: 700; }}
QLabel#BrandTag {{ color: {c['text_3']}; font-size: 8.5pt; }}
QLabel#NavSection {{ color: {c['text_3']}; font-size: 8pt; font-weight: 600; letter-spacing: 1px;
                    padding: 14px 12px 6px 12px; }}
QPushButton#NavButton {{
    text-align: left; padding: 9px 12px; border: none; border-radius: {r['md']}px;
    background: transparent; color: {c['text_2']}; font-size: 10pt;
}}
QPushButton#NavButton:hover {{ background: {c['surface']}; color: {c['text']}; }}
QPushButton#NavButton:checked {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {rgba(c['primary'], 0.22)},
                                stop:1 {rgba(c['secondary'], 0.06)});
    color: {c['text']}; font-weight: 600;
}}
QLabel#NavBadge {{ background: {c['primary']}; color: white; border-radius: 9px; padding: 0 6px;
                  font-size: 8pt; font-weight: 700; min-width: 8px; }}
QFrame#SystemCard {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: {r['lg']}px; }}
QFrame#SystemCard:hover {{ border-color: {c['border_strong']}; }}

/* ------------------------------------------------------------- tipografi */
QLabel#PageTitle {{ font-family: "Segoe UI Variable Display", "Segoe UI"; font-size: 19pt; font-weight: 700; }}
QLabel#PageSubtitle {{ color: {c['text_2']}; font-size: 10pt; }}
QLabel#CardTitle {{ font-size: 11pt; font-weight: 600; }}
QLabel#CardSubtitle, QLabel#Muted {{ color: {c['text_2']}; }}
QLabel#Faint {{ color: {c['text_3']}; font-size: 9pt; }}
QLabel#StatValue {{ font-family: "Segoe UI Variable Display", "Segoe UI"; font-size: 22pt; font-weight: 700; }}
QLabel#StatLabel {{ color: {c['text_2']}; font-size: 9.5pt; }}
QLabel#Eyebrow {{ color: {c['primary_hover']}; font-size: 8.5pt; font-weight: 700; letter-spacing: 1px; }}
QLabel#Mono {{ font-family: "Cascadia Mono", "Consolas", monospace; color: {c['text_2']}; font-size: 9pt; }}

/* ------------------------------------------------------------- kartlar */
QFrame#Card {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: {r['lg']}px; }}
QFrame#HeroCard {{
    border: 1px solid {rgba(c['primary'], 0.35)}; border-radius: {r['xl']}px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {rgba(c['primary'], 0.20)},
                                stop:0.55 {rgba(c['secondary'], 0.08)}, stop:1 {c['surface']});
}}
QFrame#DropZone {{ background: {rgba(c['primary'], 0.04)}; border: 2px dashed {c['border_strong']};
                  border-radius: {r['xl']}px; }}
QFrame#DropZone[active="true"] {{ background: {rgba(c['primary'], 0.12)}; border-color: {c['primary']}; }}
QFrame#Segmented {{ background: {c['surface_2']}; border: 1px solid {c['border_strong']};
                   border-radius: {r['md']}px; }}
QPushButton#Segment {{ background: transparent; border: none; border-radius: 7px; padding: 6px 16px;
                      color: {c['text_2']}; font-weight: 500; }}
QPushButton#Segment:hover {{ color: {c['text']}; background: {c['surface_hover']}; }}
QPushButton#Segment:checked {{ background: {grad}; color: white; font-weight: 600; }}
QPushButton#Segment:disabled {{ color: {c['text_3']}; }}
QFrame#NoticeCard {{ background: {rgba(c['info'], 0.07)}; border: 1px solid {rgba(c['info'], 0.25)};
                    border-radius: {r['lg']}px; }}
QFrame#ActionCard {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: {r['lg']}px; }}
QFrame#ActionCard:hover {{ background: {c['surface_hover']}; border-color: {rgba(c['primary'], 0.55)}; }}
QFrame#ActionCard:focus {{ border-color: {c['primary']}; }}
QFrame#ListRow {{ background: transparent; border-radius: {r['md']}px; }}
QFrame#ListRow:hover {{ background: {c['surface_2']}; }}
QFrame#Divider {{ background: {c['border']}; max-height: 1px; min-height: 1px; border: none; }}
QFrame#Swatch {{ border-radius: {r['md']}px; border: 2px solid {c['border']}; }}
QFrame#Swatch[selected="true"] {{ border: 2px solid {c['text']}; }}
QFrame#Swatch:hover {{ border-color: {c['text_2']}; }}

/* ------------------------------------------------------------- etiketler */
QLabel#Pill {{ border-radius: 10px; padding: 3px 10px; font-size: 8.5pt; font-weight: 600; }}
QLabel#Pill[tone="success"] {{ background: {rgba(c['success'], 0.12)}; color: {c['success']}; }}
QLabel#Pill[tone="warning"] {{ background: {rgba(c['warning'], 0.12)}; color: {c['warning']}; }}
QLabel#Pill[tone="danger"] {{ background: {rgba(c['danger'], 0.12)}; color: {c['danger']}; }}
QLabel#Pill[tone="info"] {{ background: {rgba(c['info'], 0.12)}; color: {c['info']}; }}
QLabel#Pill[tone="accent"] {{ background: {rgba(c['primary'], 0.16)}; color: {c['primary_hover']}; }}
QLabel#Pill[tone="neutral"] {{ background: {c['surface_2']}; color: {c['text_2']}; }}

/* ------------------------------------------------------------- düğmeler */
QPushButton {{
    background: {c['surface_2']}; border: 1px solid {c['border_strong']}; border-radius: {r['md']}px;
    padding: 8px 16px; color: {c['text']}; font-weight: 500;
}}
QPushButton:hover {{ background: {c['surface_hover']}; border-color: {rgba(c['primary'], 0.6)}; }}
QPushButton:pressed {{ background: {c['border']}; }}
QPushButton:focus {{ border-color: {c['primary']}; }}
QPushButton:disabled {{ color: {c['text_3']}; background: {c['surface']}; border-color: {c['border']}; }}
QPushButton#Primary {{ background: {grad}; border: none; color: white; font-weight: 600; padding: 9px 18px; }}
QPushButton#Primary:hover {{ background: {grad_hover}; }}
QPushButton#Primary:pressed {{ background: {c['primary']}; }}
QPushButton#Ghost {{ background: transparent; border: none; color: {c['text_2']}; padding: 6px 10px; }}
QPushButton#Ghost:hover {{ color: {c['text']}; background: {c['surface_2']}; }}
QPushButton#Link {{ background: transparent; border: none; color: {c['primary_hover']}; padding: 4px 6px; font-weight: 600; }}
QPushButton#Link:hover {{ color: {c['text']}; }}
QPushButton#Danger {{ color: {c['danger']}; }}
QPushButton#Danger:hover {{ border-color: {c['danger']}; background: {rgba(c['danger'], 0.08)}; }}
QPushButton#IconButton {{ background: transparent; border: none; padding: 4px; border-radius: 6px; }}
QPushButton#IconButton:hover {{ background: {c['surface_hover']}; }}

/* ------------------------------------------------------------- girişler */
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {{
    background: {c['surface_2']}; border: 1px solid {c['border_strong']}; border-radius: {r['md']}px;
    padding: 7px 10px; selection-background-color: {c['primary']}; selection-color: white;
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover {{ border-color: {c['text_3']}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{ border-color: {c['primary']}; }}
QLineEdit[readOnly="true"] {{ color: {c['text_2']}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox QAbstractItemView {{
    background: {c['surface_2']}; border: 1px solid {c['border_strong']}; border-radius: 8px; padding: 4px;
    selection-background-color: {rgba(c['primary'], 0.3)}; selection-color: {c['text']};
}}
QSpinBox {{ padding-left: 12px; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 18px; border: none; background: transparent; }}

/* ------------------------------------------------------------- tablo */
QTableWidget {{
    background: {c['surface']}; border: 1px solid {c['border']}; border-radius: {r['lg']}px;
    gridline-color: transparent; alternate-background-color: {c['surface_2']};
    selection-background-color: {rgba(c['primary'], 0.22)}; selection-color: {c['text']};
}}
QTableWidget::item {{ padding: 6px 8px; border: none; }}
QHeaderView::section {{
    background: {c['surface']}; color: {c['text_3']}; border: none; border-bottom: 1px solid {c['border']};
    padding: 10px 8px; font-size: 8.5pt; font-weight: 700;
}}
QTableCornerButton::section {{ background: {c['surface']}; border: none; }}

/* ------------------------------------------------------------- ilerleme */
QProgressBar {{ background: {c['surface_2']}; border: none; border-radius: 3px; max-height: 6px; min-height: 6px; }}
QProgressBar::chunk {{ background: {grad}; border-radius: 3px; }}

/* ------------------------------------------------------------- kaydırma çubukları */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
QScrollBar::handle:vertical {{ background: {c['border_strong']}; border-radius: 3px; min-height: 36px; }}
QScrollBar::handle:vertical:hover {{ background: {c['text_3']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px 4px; }}
QScrollBar::handle:horizontal {{ background: {c['border_strong']}; border-radius: 3px; min-width: 36px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
    width: 0; height: 0; background: transparent; }}

/* ------------------------------------------------------------- toast & diyaloglar */
QFrame#Toast {{ background: {c['surface_2']}; border: 1px solid {c['border_strong']}; border-radius: {r['lg']}px; }}
QLabel#ToastTitle {{ font-weight: 600; }}
QLabel#ToastBody {{ color: {c['text_2']}; }}
QDialog, QMessageBox {{ background: {c['surface']}; }}
QStatusBar {{ background: {c['sidebar']}; color: {c['text_3']}; border-top: 1px solid {c['border']}; }}
"""

"""Porcelain Clockwork Commander dual-theme tokens and QSS builder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

ThemeName = Literal["dark", "light"]


def resolve_font_family(candidates: tuple[str, ...], fallback: str) -> str:
    """Pick the first installed font family so Qt QSS never stalls on missing fonts."""
    if QApplication.instance() is not None:
        installed = set(QFontDatabase.families())
        for name in candidates:
            if name in installed:
                return name
    return fallback


@dataclass(frozen=True)
class ThemeTokens:
    """Semantic color and typography tokens for a VidEdit UI theme."""

    name: ThemeName
    display_name: str
    bg_canvas: str
    bg_surface: str
    bg_recessed: str
    bg_popover: str
    color_primary: str
    color_primary_text: str
    color_secondary: str
    text_primary: str
    text_muted: str
    accent_cobalt: str
    accent_crimson: str
    border_subtle: str
    border_hover: str
    primary_inset_border: str
    success_bg: str
    success_border: str
    success_text: str
    watermark_rgba: tuple[int, int, int, int]
    timeline_span_start: tuple[int, int, int, int]
    timeline_span_end: tuple[int, int, int, int]


LIGHT_THEME = ThemeTokens(
    name="light",
    display_name="Alabaster Light",
    bg_canvas="#FAF9FC",
    bg_surface="#EFEBF4",
    bg_recessed="#E5DFEC",
    bg_popover="#FAF9FC",
    color_primary="#2A2333",
    color_primary_text="#FAF9FC",
    color_secondary="#8C768E",
    text_primary="#2A2333",
    text_muted="#6E5D75",
    accent_cobalt="#1B54B8",
    accent_crimson="#9E1933",
    border_subtle="#D5CDDE",
    border_hover="#9F90B0",
    primary_inset_border="rgba(255, 255, 255, 0.22)",
    success_bg="#E8F4EE",
    success_border="#1B54B8",
    success_text="#1D4D3A",
    watermark_rgba=(42, 35, 51, 11),
    timeline_span_start=(27, 84, 184, 52),
    timeline_span_end=(158, 25, 51, 52),
)

DARK_THEME = ThemeTokens(
    name="dark",
    display_name="Midnight Dark",
    bg_canvas="#110E16",
    bg_surface="#1B1623",
    bg_recessed="#0B090F",
    bg_popover="#241E2F",
    color_primary="#F4F1F8",
    color_primary_text="#110E16",
    color_secondary="#A597B2",
    text_primary="#F4F1F8",
    text_muted="#A597B2",
    accent_cobalt="#3D7EFF",
    accent_crimson="#E0284A",
    border_subtle="#2E263B",
    border_hover="#463A59",
    primary_inset_border="rgba(17, 14, 22, 0.20)",
    success_bg="#161C29",
    success_border="#3D7EFF",
    success_text="#C8D9FF",
    watermark_rgba=(213, 205, 222, 10),
    timeline_span_start=(61, 126, 255, 68),
    timeline_span_end=(224, 40, 74, 68),
)


def get_theme(name: str) -> ThemeTokens:
    """Resolve a theme name safely, defaulting to Midnight Dark."""
    if name == "light":
        return LIGHT_THEME
    return DARK_THEME


def build_stylesheet(t: ThemeTokens) -> str:
    """Generate the Qt stylesheet for the given ThemeTokens."""
    display_font = resolve_font_family(("Cinzel", "Cormorant Garamond", "Georgia"), "Georgia")
    ui_font = resolve_font_family(("Plus Jakarta Sans", "Inter", "Segoe UI"), "Segoe UI")
    mono_font = resolve_font_family(("JetBrains Mono", "Cascadia Mono", "Consolas"), "Consolas")

    return f"""
QMainWindow, QWidget#MainSurface, QDialog {{
    background-color: {t.bg_canvas};
    color: {t.text_primary};
    font-family: "{ui_font}";
    font-size: 13px;
}}

QWidget {{
    color: {t.text_primary};
    font-family: "{ui_font}";
    font-size: 13px;
}}

QFrame#TitleBar {{
    background-color: {t.bg_recessed};
    border-bottom: 1px solid {t.border_subtle};
}}

QLabel#TitleBarText {{
    font-family: "{display_font}";
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 2px;
    color: {t.text_primary};
    background: transparent;
}}

QPushButton#WinBtn {{
    background-color: transparent;
    border: none;
    border-radius: 0px;
    color: {t.color_secondary};
    padding: 0px;
    font-family: "Segoe UI", sans-serif;
    font-size: 13px;
}}

QPushButton#WinBtn:hover {{
    background-color: {t.bg_surface};
    color: {t.text_primary};
}}

QPushButton#WinCloseBtn {{
    background-color: transparent;
    border: none;
    border-radius: 0px;
    color: {t.color_secondary};
    padding: 0px;
    font-family: "Segoe UI", sans-serif;
    font-size: 14px;
}}

QPushButton#WinCloseBtn:hover {{
    background-color: {t.accent_crimson};
    color: #FAF9FC;
}}

QFrame#Card, QFrame#SidebarCard {{
    background-color: {t.bg_surface};
    border: 1px solid {t.border_subtle};
    border-radius: 3px;
}}

QFrame#VideoFrame {{
    background-color: {t.bg_recessed};
    border: 1px solid {t.border_subtle};
    border-radius: 3px;
}}

QFrame#StatusBar {{
    background-color: {t.bg_recessed};
    border-top: 1px solid {t.border_subtle};
}}

QLabel#SectionTitle {{
    font-family: "{display_font}";
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 1.2px;
    color: {t.text_primary};
    background: transparent;
}}

QLabel#DropTitle {{
    font-family: "{display_font}";
    font-size: 18px;
    font-weight: 600;
    letter-spacing: 1px;
    color: {t.text_primary};
    background: transparent;
}}

QLabel#MutedLabel {{
    color: {t.text_muted};
    font-size: 12px;
    background: transparent;
}}

QLabel#BadgeLabel {{
    background-color: {t.bg_recessed};
    border: 1px solid {t.border_subtle};
    border-radius: 2px;
    padding: 4px 9px;
    color: {t.text_primary};
    font-family: "{mono_font}";
    font-size: 11px;
}}

QLabel#SuccessBanner {{
    background-color: {t.success_bg};
    border: 1px solid {t.success_border};
    border-radius: 3px;
    padding: 7px 9px;
    color: {t.success_text};
    font-size: 12px;
}}

QPushButton {{
    background-color: {t.bg_canvas};
    border: 1px solid {t.border_subtle};
    border-radius: 3px;
    padding: 6px 12px;
    color: {t.text_primary};
    font-weight: 500;
}}

QPushButton:hover {{
    background-color: {t.bg_popover};
    border: 1px solid {t.border_hover};
}}

QPushButton:pressed {{
    background-color: {t.bg_recessed};
}}

QPushButton:disabled {{
    background-color: {t.bg_recessed};
    color: {t.color_secondary};
    border-color: {t.border_subtle};
}}

QPushButton[danger="true"]:hover {{
    background-color: {t.bg_popover};
    border: 1px solid {t.accent_crimson};
    color: {t.accent_crimson};
}}

QPushButton[primary="true"] {{
    background-color: {t.color_primary};
    border: 1px solid {t.border_hover};
    border-radius: 3px;
    color: {t.color_primary_text};
    font-weight: 600;
    padding: 9px 16px;
}}

QPushButton[primary="true"]:hover {{
    border: 1px solid {t.accent_cobalt};
}}

QPushButton[primary="true"]:disabled {{
    background-color: {t.bg_recessed};
    border-color: {t.border_subtle};
    color: {t.color_secondary};
}}

QPushButton[pill="true"] {{
    padding: 6px 10px;
    border-radius: 2px;
    background-color: {t.bg_recessed};
    border: 1px solid {t.border_subtle};
    color: {t.text_muted};
}}

QPushButton[pill="true"]:hover {{
    background-color: {t.bg_popover};
    border: 1px solid {t.border_hover};
    color: {t.text_primary};
}}

QPushButton[pill="true"]:checked {{
    background-color: {t.bg_canvas};
    border: 1px solid {t.border_hover};
    border-bottom: 2px solid {t.accent_cobalt};
    color: {t.text_primary};
    font-weight: 600;
}}

QPushButton#StatusBtn {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 2px;
    padding: 2px 8px;
    color: {t.text_muted};
    font-family: "{mono_font}";
    font-size: 11px;
}}

QPushButton#StatusBtn:hover {{
    background-color: {t.bg_surface};
    border: 1px solid {t.border_hover};
    color: {t.text_primary};
}}

QLineEdit, QComboBox, QDoubleSpinBox, QPlainTextEdit {{
    background-color: {t.bg_recessed};
    border: 1px solid {t.border_subtle};
    border-radius: 2px;
    padding: 6px 9px;
    color: {t.text_primary};
    font-family: "{ui_font}";
}}

QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {{
    border: 1px solid {t.accent_cobalt};
}}

QComboBox::drop-down {{
    border: none;
    width: 22px;
}}

QComboBox QAbstractItemView {{
    background-color: {t.bg_popover};
    border: 1px solid {t.border_subtle};
    selection-background-color: {t.accent_cobalt};
    selection-color: #FAF9FC;
    color: {t.text_primary};
}}

QCheckBox {{
    spacing: 8px;
    color: {t.text_primary};
    background: transparent;
}}

QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border-radius: 2px;
    border: 1px solid {t.border_hover};
    background-color: {t.bg_recessed};
}}

QCheckBox::indicator:checked {{
    background-color: {t.accent_cobalt};
    border-color: {t.accent_cobalt};
}}

QProgressBar {{
    background-color: {t.bg_recessed};
    border: 1px solid {t.border_subtle};
    border-radius: 2px;
    text-align: center;
    height: 16px;
    color: {t.text_primary};
    font-family: "{mono_font}";
    font-size: 11px;
    font-weight: 600;
}}

QProgressBar::chunk {{
    background-color: {t.accent_cobalt};
    border-radius: 1px;
}}

QSlider::groove:horizontal {{
    height: 3px;
    background: {t.border_subtle};
    border-radius: 1px;
}}

QSlider::sub-page:horizontal {{
    background: {t.accent_cobalt};
    border-radius: 1px;
}}

QSlider::handle:horizontal {{
    background: {t.text_primary};
    border: 1px solid {t.accent_cobalt};
    width: 10px;
    height: 10px;
    margin: -4px 0;
    border-radius: 2px;
}}

QListWidget#CommandList {{
    background-color: {t.bg_recessed};
    border: 1px solid {t.border_subtle};
    border-radius: 2px;
    outline: none;
}}

QListWidget#CommandList::item {{
    padding: 8px 10px;
    border-bottom: 1px solid {t.border_subtle};
    color: {t.text_primary};
}}

QListWidget#CommandList::item:selected {{
    background-color: {t.bg_surface};
    border-left: 2px solid {t.accent_cobalt};
    color: {t.text_primary};
}}

QPushButton#EdgeDrawerBtn {{
    background-color: {t.bg_surface};
    border: 1px solid {t.border_subtle};
    border-radius: 3px;
    padding: 6px 2px;
    color: {t.text_muted};
    font-family: "{mono_font}";
    font-size: 10px;
    font-weight: 600;
}}

QPushButton#EdgeDrawerBtn:hover {{
    background-color: {t.bg_popover};
    border: 1px solid {t.border_hover};
    color: {t.text_primary};
}}

QPushButton#EdgeDrawerBtn:checked {{
    background-color: {t.bg_popover};
    border: 1px solid {t.border_hover};
    border-right: 2px solid {t.accent_cobalt};
    color: {t.text_primary};
}}

QPushButton#EdgeDrawerBtn[downloading="true"] {{
    border-left: 2px solid {t.accent_crimson};
}}

QListWidget#DownloadedClipsList {{
    background-color: {t.bg_recessed};
    border: 1px solid {t.border_subtle};
    border-radius: 2px;
    outline: none;
    font-size: 12px;
}}

QListWidget#DownloadedClipsList::item {{
    padding: 5px 8px;
    border-bottom: 1px solid {t.border_subtle};
    color: {t.text_primary};
}}

QListWidget#DownloadedClipsList::item:selected {{
    background-color: {t.bg_surface};
    border-left: 2px solid {t.accent_cobalt};
}}
"""

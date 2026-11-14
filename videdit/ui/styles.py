"""Stylesheet helpers for VidEdit (delegates to the Porcelain Clockwork Commander theme system)."""

from videdit.ui.theme import DARK_THEME, LIGHT_THEME, build_stylesheet

DARK_STYLESHEET = build_stylesheet(DARK_THEME)
LIGHT_STYLESHEET = build_stylesheet(LIGHT_THEME)

__all__ = ["DARK_STYLESHEET", "LIGHT_STYLESHEET", "DARK_THEME", "LIGHT_THEME", "build_stylesheet"]

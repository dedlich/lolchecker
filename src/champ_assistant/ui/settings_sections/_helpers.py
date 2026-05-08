"""Shared visual helpers for the settings dialog tabs.

All styling pulls from ``ui.styles`` design tokens — per the design-system
lockdown linter, no inline px / hex values are allowed. Match the
title-bar / panel visual language so the dialog doesn't feel grafted on.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import styles


def scrolling_page() -> QWidget:
    """Container widget used as a tab page. Tabs share their parent
    QTabWidget's height; long content scrolls naturally inside its
    tab if needed."""
    page = QWidget()
    page.setStyleSheet(f"background: {styles.BG_PRIMARY};")
    return page


def vertical(parent: QWidget) -> QVBoxLayout:
    layout = QVBoxLayout(parent)
    layout.setContentsMargins(
        styles.SPACING_GRID, styles.SPACING_GRID,
        styles.SPACING_GRID, styles.SPACING_GRID,
    )
    layout.setSpacing(styles.SPACING_GRID)
    return layout


def section_header(text: str) -> QLabel:
    """Section title with a small accent dot prefix — mirrors the
    title-bar pattern from v1.7.0 so the visual rhythm is consistent
    across the app."""
    label = QLabel(f"●  {text.upper()}")
    label.setStyleSheet(
        f"color: {styles.ACCENT};"
        f" font-size: {styles.FS_LABEL}px;"
        " font-weight: 700;"
        " letter-spacing: 1.6px; padding: 12px 0 4px 0;"
    )
    return label


def hint_label(text: str) -> QLabel:
    """Caption-style hint underneath a control. Slightly indented so
    it visually anchors to the control above without competing with
    the next section header."""
    label = QLabel(text)
    label.setStyleSheet(
        f"color: {styles.TEXT_MUTED};"
        f" font-size: {styles.FS_CAPTION}px;"
        f" padding-left: 24px; padding-bottom: 6px;"
    )
    label.setWordWrap(True)
    return label


def checkbox(label: str, checked: bool) -> QCheckBox:
    cb = QCheckBox(label)
    cb.setChecked(checked)
    cb.setCursor(Qt.CursorShape.PointingHandCursor)
    cb.setStyleSheet(
        f"QCheckBox {{ color: {styles.TEXT_PRIMARY};"
        f" font-size: {styles.FS_BODY}px;"
        f" spacing: 8px; padding: 2px 0; }}"
        f" QCheckBox::indicator {{ width: 16px; height: 16px;"
        f" border: 1px solid {styles.BORDER};"
        f" border-radius: 3px; background-color: {styles.BG_TERTIARY}; }}"
        f" QCheckBox::indicator:hover {{ border-color: {styles.ACCENT}; }}"
        f" QCheckBox::indicator:checked {{ background-color: {styles.ACCENT};"
        f" border-color: {styles.ACCENT}; }}"
    )
    return cb


def flat_button(text: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(
        f"QPushButton {{ background-color: {styles.BG_TERTIARY};"
        f" color: {styles.TEXT_SECONDARY};"
        f" border: 1px solid {styles.BORDER};"
        f" border-radius: {styles.RADIUS_SMALL}px;"
        f" padding: 5px 14px; font-size: {styles.FS_LABEL}px; font-weight: 600; }}"
        f" QPushButton:hover {{ border-color: {styles.WARNING};"
        f" color: {styles.TEXT_PRIMARY}; }}"
    )
    return btn


def hotkey_button_stylesheet() -> str:
    return (
        f"QPushButton {{ background-color: {styles.BG_TERTIARY};"
        f" color: {styles.ACCENT};"
        f" border: 1px solid {styles.BORDER};"
        f" border-radius: {styles.RADIUS_SMALL}px;"
        f" padding: 5px 12px; font-family: {styles.FONT_MONO};"
        f" font-weight: 700; font-size: {styles.FS_LABEL}px; }}"
        f" QPushButton:hover {{ border-color: {styles.ACCENT};"
        f" background-color: {styles.BG_ELEVATED}; }}"
    )


def tab_widget_stylesheet() -> str:
    """Tabs inherit the panel-token visual language: dark background,
    accent-bordered active tab, muted inactive tabs."""
    return (
        f"QTabWidget::pane {{"
        f"  background-color: {styles.BG_SECONDARY};"
        f"  border: 1px solid {styles.BORDER};"
        f"  border-radius: {styles.RADIUS_SMALL}px;"
        f"  top: -1px;"
        " }"
        f"QTabBar::tab {{"
        f"  background-color: {styles.BG_TERTIARY};"
        f"  color: {styles.TEXT_MUTED};"
        f"  border: 1px solid {styles.BORDER};"
        f"  border-bottom: none;"
        f"  border-top-left-radius: {styles.RADIUS_SMALL}px;"
        f"  border-top-right-radius: {styles.RADIUS_SMALL}px;"
        f"  padding: 8px 16px;"
        f"  font-size: {styles.FS_BODY}px;"
        f"  font-weight: 600;"
        f"  margin-right: 2px;"
        " }"
        f"QTabBar::tab:selected {{"
        f"  background-color: {styles.BG_SECONDARY};"
        f"  color: {styles.ACCENT};"
        " }"
        f"QTabBar::tab:hover:!selected {{"
        f"  color: {styles.TEXT_PRIMARY};"
        " }"
    )

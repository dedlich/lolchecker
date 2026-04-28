"""Modal dialog that captures the next pressed key combination.

Used by :class:`SettingsDialog` to let users rebind hotkeys. Translates
the captured Qt key event into the canonical "Ctrl+Alt+H" string format
that ``hotkey_config.parse_combo`` understands.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QDialog, QLabel, QVBoxLayout

from .. import hotkey_config
from . import styles

# Qt key codes that are pure modifiers — we ignore these on their own
# and wait for the user to add a regular key.
_MODIFIER_KEYS = {
    Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt,
    Qt.Key.Key_Meta, Qt.Key.Key_AltGr, Qt.Key.Key_CapsLock,
}


class KeyCaptureDialog(QDialog):
    """Captures the next non-modifier keypress + its active modifiers.

    Returns the canonical combo string via :meth:`captured_combo`.
    Esc cancels (``rejected`` signal).
    """

    def __init__(self, *, parent=None, current_label: str = "") -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.setWindowTitle("Set hotkey")
        self.setModal(True)
        self.setFixedSize(360, 140)
        self.setStyleSheet(
            f"QDialog {{ background-color: {styles.BG_PRIMARY}; }}"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(12)

        title = QLabel("Press your key combination")
        title.setStyleSheet(
            f"font-size: {styles.FS_HEADING}px; font-weight: 700;"
            f" color: {styles.TEXT_PRIMARY};"
        )
        outer.addWidget(title)

        self._preview = QLabel(current_label or "—")
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setStyleSheet(
            f"background-color: {styles.BG_TERTIARY};"
            f" color: {styles.ACCENT};"
            f" font-family: {styles.FONT_MONO};"
            f" font-size: 18px; font-weight: 700;"
            f" padding: 14px; border-radius: {styles.RADIUS}px;"
            f" border: 1px solid {styles.BORDER};"
            f" letter-spacing: 0.5px;"
        )
        outer.addWidget(self._preview)

        hint = QLabel("Esc cancels   ·   modifier-only combos rejected")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: {styles.FS_CAPTION}px;"
        )
        outer.addWidget(hint)

        self._captured: str | None = None

    def captured_combo(self) -> str | None:
        return self._captured

    # -- input handling ---------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.reject()
            return
        if key in _MODIFIER_KEYS:
            # Update preview to show the partial chord while the user is
            # still pressing modifiers, but don't accept it yet.
            self._preview.setText(self._format_modifiers(event.modifiers()))
            return
        combo = self._compose(key, event.modifiers())
        if combo is None:
            return  # unsupported key — keep waiting
        if not hotkey_config.is_valid_combo(combo):
            # Validator agrees the chord is incomplete (no non-modifier);
            # show the partial state and let the user keep pressing.
            self._preview.setText(combo)
            return
        self._captured = combo
        self._preview.setText(combo)
        self.accept()

    @staticmethod
    def _format_modifiers(mods: Qt.KeyboardModifier) -> str:
        parts = []
        if mods & Qt.KeyboardModifier.ControlModifier: parts.append("Ctrl")
        if mods & Qt.KeyboardModifier.AltModifier:     parts.append("Alt")
        if mods & Qt.KeyboardModifier.ShiftModifier:   parts.append("Shift")
        if mods & Qt.KeyboardModifier.MetaModifier:    parts.append("Win")
        parts.append("…")
        return "+".join(parts)

    @staticmethod
    def _compose(key: int, mods: Qt.KeyboardModifier) -> str | None:
        parts: list[str] = []
        if mods & Qt.KeyboardModifier.ControlModifier: parts.append("Ctrl")
        if mods & Qt.KeyboardModifier.AltModifier:     parts.append("Alt")
        if mods & Qt.KeyboardModifier.ShiftModifier:   parts.append("Shift")
        if mods & Qt.KeyboardModifier.MetaModifier:    parts.append("Win")

        # Letters: Qt.Key.Key_A == 0x41
        if 0x41 <= key <= 0x5A:
            parts.append(chr(key))
            return "+".join(parts)
        # Digits: Qt.Key.Key_0 == 0x30
        if 0x30 <= key <= 0x39:
            parts.append(chr(key))
            return "+".join(parts)
        # Function keys: Qt.Key.Key_F1 == 0x01000030
        for n in range(1, 25):
            if key == int(Qt.Key.Key_F1) + (n - 1):
                parts.append(f"F{n}")
                return "+".join(parts)
        return None  # unsupported key

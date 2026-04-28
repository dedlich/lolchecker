"""Highlights freshly-crossed power spikes for a few seconds.

The LCDA snapshot exposes ``new_spikes`` whenever the active player just
hit a level or item milestone. The panel shows the most recent spike
prominently, then fades to a compact "current state" badge after the
attention window expires.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from ..lcda.power_spikes import PowerSpike
from ..lcda.source import LcdaSnapshot
from . import styles

ATTENTION_WINDOW_S = 12.0


class PowerSpikePanel(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setProperty("panel", True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("Live Game — Spikes")
        title.setObjectName("sectionTitle")
        header.addWidget(title, 1)
        self._state_label = QLabel("")
        self._state_label.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-family: {styles.FONT_MONO};"
            f" font-size: {styles.FS_LABEL}px;"
            f" background-color: {styles.BG_TERTIARY};"
            f" padding: 2px 8px; border-radius: {styles.RADIUS_SMALL}px;"
        )
        header.addWidget(self._state_label, 0, Qt.AlignmentFlag.AlignRight)
        outer.addLayout(header)

        self._headline = QLabel("")
        self._headline.setStyleSheet(
            f"color: {styles.WARNING}; font-weight: 800;"
            f" font-size: {styles.FS_HEADING}px; letter-spacing: 0.6px;"
        )
        outer.addWidget(self._headline)
        self._detail = QLabel("")
        self._detail.setStyleSheet(
            f"color: {styles.TEXT_SECONDARY}; font-size: {styles.FS_LABEL}px;"
        )
        self._detail.setWordWrap(True)
        outer.addWidget(self._detail)

        self._latest_spike: PowerSpike | None = None
        self._latest_spike_at: float = 0.0
        # Fade animation now driven by the central scheduler's 1 Hz tick
        # via ``connect_scheduler`` — no per-widget QTimer (P5).
        self.hide()

    def connect_scheduler(self, scheduler) -> None:  # type: ignore[no-untyped-def]
        scheduler.tick.connect(self._refresh_visibility)

    def update_snapshot(self, snapshot: LcdaSnapshot | None) -> None:
        if snapshot is None:
            self.hide()
            return
        self.show()

        # Compact state line: "Lvl 9 · 2 items"
        items_word = "item" if snapshot.active_items == 1 else "items"
        self._state_label.setText(
            f"Lvl {snapshot.active_level} · {snapshot.active_items} {items_word}"
        )

        if snapshot.new_spikes:
            self._latest_spike = snapshot.new_spikes[-1]
            self._latest_spike_at = time.monotonic()
        # Repaint immediately so the fresh spike is visible; the central
        # scheduler's 1 Hz tick keeps fading it out from there.
        self._refresh_visibility()

    def _refresh_visibility(self) -> None:
        spike = self._latest_spike
        if spike is None:
            self._headline.setText("")
            self._detail.setText("Track your level + item count.")
            self._detail.setStyleSheet(f"color: {styles.TEXT_MUTED}; font-size: 11px;")
            return
        elapsed = time.monotonic() - self._latest_spike_at
        if elapsed > ATTENTION_WINDOW_S:
            self._headline.setText("")
            self._detail.setText("Track your level + item count.")
            self._detail.setStyleSheet(f"color: {styles.TEXT_MUTED}; font-size: 11px;")
            return
        # Bright while inside the attention window, dimming towards the end.
        ratio = max(0.0, 1.0 - (elapsed / ATTENTION_WINDOW_S))
        # Convert hex like "#FFB84A" to rgba with alpha
        alpha = int(40 + 215 * ratio)
        self._headline.setText(spike.label.upper())
        self._headline.setStyleSheet(
            f"color: rgba(255, 184, 74, {alpha}); font-weight: 700; font-size: 14px;"
        )
        self._detail.setText(spike.detail)
        self._detail.setStyleSheet(f"color: {styles.TEXT_SECONDARY}; font-size: 11px;")

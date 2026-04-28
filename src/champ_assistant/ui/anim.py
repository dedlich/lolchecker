"""Subtle animation helpers — fade in/out + property tweens.

Single source for the 180 ms default cadence so every transition in the
app feels like it comes from the same hand. Spec calls for 150–200 ms,
no bouncing, no flashy motion.
"""
from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QWidget

from . import styles


def fade_in(widget: QWidget, *, duration_ms: int | None = None,
            from_opacity: float = 0.0, to_opacity: float = 1.0) -> QPropertyAnimation:
    """Fade ``widget`` in. Show it first if hidden — animation only runs
    against visible widgets.

    Returns the animation so callers can chain or hold a strong reference
    (Qt garbage-collects animations whose owners drop the ref).
    """
    if not widget.isVisible():
        widget.show()
    effect = widget.graphicsEffect()
    if not isinstance(effect, QGraphicsOpacityEffect):
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
    effect.setOpacity(from_opacity)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration_ms or styles.ANIM_DEFAULT_MS)
    anim.setStartValue(from_opacity)
    anim.setEndValue(to_opacity)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    return anim


def fade_out(widget: QWidget, *, duration_ms: int | None = None,
             then_hide: bool = True) -> QPropertyAnimation:
    """Fade ``widget`` to 0 opacity, optionally hide it on completion."""
    effect = widget.graphicsEffect()
    if not isinstance(effect, QGraphicsOpacityEffect):
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        effect.setOpacity(1.0)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration_ms or styles.ANIM_DEFAULT_MS)
    anim.setStartValue(effect.opacity())
    anim.setEndValue(0.0)
    anim.setEasingCurve(QEasingCurve.Type.InCubic)
    if then_hide:
        anim.finished.connect(widget.hide)
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    return anim


__all__ = ["fade_in", "fade_out", "Qt"]

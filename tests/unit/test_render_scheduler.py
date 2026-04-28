"""Tests for the render-throttling scheduler."""
from __future__ import annotations

import pytest
from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer

from champ_assistant.render_scheduler import RenderScheduler


@pytest.fixture
def qt_app():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


def _spin(ms: int) -> None:
    """Spin the Qt event loop briefly so timers fire."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def test_repaint_request_coalesces(qt_app) -> None:  # type: ignore[no-untyped-def]
    sched = RenderScheduler(max_fps=60)  # ~16ms min interval
    received = 0

    def on_repaint() -> None:
        nonlocal received
        received += 1

    sched.repaint.connect(on_repaint)
    # Bursts of 100 requests within one frame must coalesce to a single fire.
    for _ in range(100):
        sched.request_repaint()
    _spin(50)
    assert received == 1
    assert sched.frame_count == 1


def test_no_repaint_when_no_request(qt_app) -> None:  # type: ignore[no-untyped-def]
    sched = RenderScheduler()
    received = 0

    def on_repaint() -> None:
        nonlocal received
        received += 1

    sched.repaint.connect(on_repaint)
    _spin(50)
    assert received == 0


def test_tick_fires_at_one_hz(qt_app) -> None:  # type: ignore[no-untyped-def]
    sched = RenderScheduler(tick_hz=10.0)  # 100ms for fast test
    received = 0

    def on_tick() -> None:
        nonlocal received
        received += 1

    sched.tick.connect(on_tick)
    sched.start()
    _spin(350)  # ~3 ticks at 10 Hz
    sched.stop()
    assert 2 <= received <= 4


def test_frame_count_resets(qt_app) -> None:  # type: ignore[no-untyped-def]
    sched = RenderScheduler()
    sched.request_repaint()
    _spin(50)
    assert sched.frame_count == 1
    sched.reset_frame_count()
    assert sched.frame_count == 0

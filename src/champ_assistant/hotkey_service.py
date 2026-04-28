"""Global Windows hotkey service.

Implements the spec'd architecture:

  ┌────────────────────┐    WM_HOTKEY     ┌──────────────────┐
  │ Listener Thread    │ ─────────────▶  │  Qt Main Thread  │
  │ (RegisterHotKey +  │   queued signal  │  StateStore.update │
  │  GetMessageW loop) │                   │  → subscribers      │
  └────────────────────┘                   └──────────────────┘

Why ctypes / not Qt: Qt's QShortcut requires keyboard focus on a Qt
widget. A click-through always-on-top overlay never has focus while the
game is being played, so QShortcut would never fire. The Win32
``RegisterHotKey`` API installs a system-wide handler that receives
WM_HOTKEY regardless of which window owns input focus.

Why a dedicated thread: ``RegisterHotKey`` is thread-affine — the thread
that registers a hotkey is the one that receives WM_HOTKEY in its message
queue. Running a GetMessage pump on the Qt main thread would block the UI;
a dedicated daemon thread blocks only itself.

Cross-thread dispatch: the listener emits ``hotkey_pressed(name)`` from
its own thread. PyQt6 auto-queues that signal across the thread boundary
when receivers are bound to the Qt main thread, so subscribers run on the
UI thread without manual ``QMetaObject.invokeMethod`` plumbing.

Cross-platform: on macOS / Linux ``start()`` is a no-op so dev work on
those hosts continues to import the module without conditionals at
every call site.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import sys
import threading
from dataclasses import dataclass

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Win32 constants (https://learn.microsoft.com/en-us/windows/win32/inputdev/)
# --------------------------------------------------------------------------
MOD_ALT      = 0x0001
MOD_CONTROL  = 0x0002
MOD_SHIFT    = 0x0004
MOD_WIN      = 0x0008
MOD_NOREPEAT = 0x4000  # don't auto-repeat while key is held

VK_H = 0x48
VK_L = 0x4C
VK_R = 0x52

WM_HOTKEY = 0x0312
WM_QUIT   = 0x0012


# Plain MSG struct so GetMessageW has somewhere to write. wintypes ships
# the basic types but not MSG / POINT — define them here.
class _POINT(ctypes.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG)]


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd",    wt.HWND),
        ("message", wt.UINT),
        ("wParam",  wt.WPARAM),
        ("lParam",  wt.LPARAM),
        ("time",    wt.DWORD),
        ("pt",      _POINT),
    ]


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class HotkeyBinding:
    """Semantic name + Win32 modifiers/vk for one global hotkey."""
    name: str        # action identifier, e.g. "toggle_overlay"
    modifiers: int   # bitwise OR of MOD_*
    vk: int          # virtual-key code (VK_*)
    label: str       # human-readable, e.g. "Ctrl+Alt+H"

    def __str__(self) -> str:
        return self.label


DEFAULT_BINDINGS: tuple[HotkeyBinding, ...] = (
    HotkeyBinding("toggle_overlay",  MOD_CONTROL | MOD_ALT, VK_H, "Ctrl+Alt+H"),
    HotkeyBinding("toggle_lock",     MOD_CONTROL | MOD_ALT, VK_L, "Ctrl+Alt+L"),
    HotkeyBinding("reset_positions", MOD_CONTROL | MOD_ALT, VK_R, "Ctrl+Alt+R"),
)


def _on_windows() -> bool:
    return sys.platform.startswith("win")


class HotkeyService(QObject):
    """Owns the listener thread + bindings. Idempotent start/stop.

    Subscribers connect to :pyattr:`hotkey_pressed`; the signal carries
    the binding's ``name`` and is delivered on the Qt main thread.
    """

    hotkey_pressed = pyqtSignal(str)

    def __init__(
        self,
        bindings: tuple[HotkeyBinding, ...] = DEFAULT_BINDINGS,
    ) -> None:
        super().__init__()
        self._bindings = bindings
        self._thread: threading.Thread | None = None
        self._thread_id: int = 0
        self._running = False
        self._lock = threading.Lock()

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        """Spin up the listener thread. Safe to call multiple times — the
        second call is a no-op as long as the first listener is alive.
        On non-Windows hosts this is always a no-op."""
        with self._lock:
            if self._running:
                return
            if not _on_windows():
                logger.info("hotkey_service: not on Windows — disabled")
                return
            self._running = True
            self._thread = threading.Thread(
                target=self._run,
                name="hotkey-service",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """Unregister hotkeys and tear down the listener. Safe to call
        from any thread, including the application's aboutToQuit handler."""
        with self._lock:
            if not self._running:
                return
            self._running = False
            thread_id = self._thread_id
            thread = self._thread
        # Wake GetMessageW so the loop sees self._running == False and
        # exits cleanly. Outside the lock to avoid holding it during a
        # cross-thread Win32 call.
        if thread_id and _on_windows():
            try:
                user32 = ctypes.windll.user32
                user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
            except OSError as exc:
                logger.warning("hotkey_service stop PostThreadMessage: %s", exc)
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        with self._lock:
            self._thread = None
            self._thread_id = 0

    # -- listener thread --------------------------------------------------

    def _run(self) -> None:
        """Runs in the dedicated daemon thread."""
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
        except OSError as exc:
            logger.warning("hotkey_service: ctypes load failed: %s", exc)
            with self._lock:
                self._running = False
            return

        # Calling GetCurrentThreadId here also triggers Win32 to allocate
        # a message queue for this thread (so RegisterHotKey can post
        # WM_HOTKEY into it).
        with self._lock:
            self._thread_id = kernel32.GetCurrentThreadId()

        registered: list[tuple[int, HotkeyBinding]] = []
        try:
            self._register_all(user32, kernel32, registered)
            self._pump_messages(user32, registered)
        except Exception:
            logger.exception("hotkey_service: listener crashed")
        finally:
            self._unregister_all(user32, registered)
            logger.info("hotkey_service: listener exited")
            with self._lock:
                # If we exited due to an internal error rather than stop()
                # having been called, mark stopped so a future start() can
                # retry.
                self._running = False

    def _register_all(
        self,
        user32,  # type: ignore[no-untyped-def]
        kernel32,  # type: ignore[no-untyped-def]
        registered: list[tuple[int, HotkeyBinding]],
    ) -> None:
        for i, binding in enumerate(self._bindings, start=1):
            ok = user32.RegisterHotKey(
                None,                               # hWnd: post to this thread
                i,                                  # id
                binding.modifiers | MOD_NOREPEAT,
                binding.vk,
            )
            if ok:
                registered.append((i, binding))
                logger.info(
                    "hotkey registered: %s -> %s",
                    binding.label, binding.name,
                )
            else:
                err = kernel32.GetLastError()
                # ERROR_HOTKEY_ALREADY_REGISTERED = 1409 → conflict with
                # another app or a prior start() that didn't clean up.
                logger.warning(
                    "hotkey register failed: %s -> %s (errno=%d)",
                    binding.label, binding.name, err,
                )

    def _pump_messages(
        self,
        user32,  # type: ignore[no-untyped-def]
        registered: list[tuple[int, HotkeyBinding]],
    ) -> None:
        msg = _MSG()
        # GetMessageW blocks until a message arrives. Returns:
        #   > 0   normal message
        #   = 0   WM_QUIT received
        #   < 0   error
        while True:
            with self._lock:
                if not self._running:
                    return
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret <= 0:
                return
            if msg.message != WM_HOTKEY:
                continue
            binding = next(
                (b for i, b in registered if i == msg.wParam),
                None,
            )
            if binding is None:
                continue
            logger.info("hotkey pressed: %s", binding.name)
            # Cross-thread queued by Qt because subscribers live on the
            # main thread (their owning QObject.thread() != ours).
            self.hotkey_pressed.emit(binding.name)

    def _unregister_all(
        self,
        user32,  # type: ignore[no-untyped-def]
        registered: list[tuple[int, HotkeyBinding]],
    ) -> None:
        for i, binding in registered:
            try:
                user32.UnregisterHotKey(None, i)
                logger.info("hotkey unregistered: %s", binding.name)
            except OSError as exc:
                logger.warning(
                    "hotkey unregister failed: %s: %s",
                    binding.name, exc,
                )

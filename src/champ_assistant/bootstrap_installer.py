"""In-place updater that runs as a hidden CLI mode.

When the previous version's ``apply_update`` extracts a new build, it
relaunches the new exe with ``--bootstrap-install`` pointing at the
real install directory. ``main()`` dispatches that path here BEFORE
any heavy imports (no Qt, no asyncio) so the install runs in a quiet
no-UI process.

The bootstrap installer:
  1. waits for the parent (old) process to exit
  2. copies the staged build over the install directory
  3. relaunches the freshly-installed exe
  4. exits

All diagnostics go to ``last-update.log`` so the next normal start can
surface failures.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _bootstrap_install(
    staged_dir: Path,
    install_dir: Path,
    *,
    parent_pid: int,
) -> int:
    """Minimal no-UI mode: wait for old app to exit, copy files, relaunch.

    Runs when the new exe is launched from the staging directory with
    --bootstrap-install. Does not import Qt. Writes diagnostics to the
    standard update log so the next normal start can surface failures.
    """
    import ctypes
    import shutil
    import time

    if sys.platform.startswith("win"):
        localappdata = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        log_path = Path(localappdata) / "ChampAssistant" / "logs" / "last-update.log"
    else:
        log_path = Path.home() / ".champ-assistant" / "logs" / "last-update.log"

    log_path.parent.mkdir(parents=True, exist_ok=True)

    def _log(msg: str) -> None:
        try:
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(f"[bootstrap] {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
        except OSError:
            pass

    try:
        log_path.write_text("", encoding="utf-8")
    except OSError:
        pass

    _log(f"start. staged={staged_dir} install={install_dir} parent_pid={parent_pid}")

    if parent_pid and sys.platform.startswith("win"):
        SYNCHRONIZE = 0x00100000
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, parent_pid)
        if handle:
            kernel32.WaitForSingleObject(handle, 30_000)
            kernel32.CloseHandle(handle)
            _log("parent exited (WaitForSingleObject)")
        else:
            _log("parent_pid not found — already exited")
    elif parent_pid:
        import subprocess as _sp
        deadline = time.time() + 30.0
        while time.time() < deadline:
            try:
                _sp.run(["kill", "-0", str(parent_pid)], capture_output=True, check=True)
            except Exception:
                break
            time.sleep(0.5)
        _log("parent gone (poll)")

    time.sleep(1)

    _log(f"copying {staged_dir} → {install_dir}")
    for attempt in range(1, 4):
        try:
            shutil.copytree(str(staged_dir), str(install_dir), dirs_exist_ok=True)
            _log("copy OK")
            break
        except OSError as exc:
            _log(f"copy attempt {attempt} failed: {exc}")
            if attempt < 3:
                time.sleep(2)
            else:
                _log("FAIL: could not copy files after 3 attempts")
                return 1

    from champ_assistant.update_check import EXE_NAME
    new_exe = install_dir / EXE_NAME
    if not new_exe.is_file():
        _log(f"FAIL: {new_exe} not found after copy")
        return 1

    try:
        import subprocess as _sp
        _sp.Popen([str(new_exe)], cwd=str(install_dir))
        _log(f"SUCCESS: launched {new_exe}")
    except OSError as exc:
        _log(f"FAIL: could not launch {new_exe}: {exc}")
        return 1

    return 0

"""GitHub Releases update checker + in-app installer.

Polls ``/repos/<owner>/<repo>/releases/latest`` once at startup, compares the
returned tag to the running app's ``__version__``, and notifies the UI when a
newer release is published. When the user clicks "install now", the app
streams the release ZIP, extracts it to a staging dir, writes a tiny sidecar
``.bat`` that waits for the app's PID to exit, swaps the install directory,
and relaunches the new exe — then quits cleanly. The bat self-deletes.

Failures (network down, GitHub API rate-limited, malformed JSON) degrade
silently — never block the app from starting.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

DEFAULT_REPO = "dedlich/lolchecker"
DEFAULT_TIMEOUT = 5.0
RELEASE_ASSET_NAME = "champ-assistant-windows.zip"
EXE_NAME = "champ-assistant.exe"

ProgressCb = Callable[[str], None] | Callable[[str], Awaitable[None]]


def _parse_version(tag: str) -> tuple[int, ...]:
    """Convert a tag like 'v0.2.0' or '0.2.0-beta.1' to a comparable tuple."""
    cleaned = tag.lstrip("v").split("-", 1)[0]
    parts = re.findall(r"\d+", cleaned)
    if not parts:
        return (0,)
    return tuple(int(p) for p in parts)


def is_newer(latest_tag: str, current_version: str) -> bool:
    """Strict-greater-than version comparison."""
    return _parse_version(latest_tag) > _parse_version(current_version)


async def fetch_latest_release(
    repo: str = DEFAULT_REPO,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, str] | None:
    """Return ``{"tag": ..., "url": ...}`` or None on any failure."""
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        kwargs: dict[str, object] = {"timeout": timeout}
        if transport is not None:
            kwargs["transport"] = transport
        async with httpx.AsyncClient(**kwargs) as client:
            response = await client.get(
                url, headers={"Accept": "application/vnd.github+json"}
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("update_check_failed: %s", exc)
        return None
    tag = data.get("tag_name")
    html_url = data.get("html_url")
    if not isinstance(tag, str) or not isinstance(html_url, str):
        return None
    return {"tag": tag, "url": html_url}


async def check_for_update(
    current_version: str,
    *,
    repo: str = DEFAULT_REPO,
    timeout: float = DEFAULT_TIMEOUT,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, str] | None:
    """If a newer release exists, return its info; otherwise None."""
    info = await fetch_latest_release(repo, timeout=timeout, transport=transport)
    if info is None:
        return None
    if is_newer(info["tag"], current_version):
        return info
    return None


def asset_download_url(tag: str, repo: str = DEFAULT_REPO) -> str:
    """Build the direct download URL for the Windows release ZIP."""
    return f"https://github.com/{repo}/releases/download/{tag}/{RELEASE_ASSET_NAME}"


def install_dir() -> Path | None:
    """Where the running exe lives, or None when not frozen."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return None


async def download_release_zip(
    url: str,
    dest: Path,
    *,
    progress: Callable[[int, int | None], None] | None = None,
    timeout: float = 60.0,
) -> None:
    """Stream the release ZIP to disk. Raises httpx.HTTPError on failure."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with (
        httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client,
        client.stream("GET", url) as response,
    ):
        response.raise_for_status()
        total_str = response.headers.get("Content-Length")
        total = int(total_str) if total_str and total_str.isdigit() else None
        received = 0
        with dest.open("wb") as fh:
            async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                fh.write(chunk)
                received += len(chunk)
                if progress is not None:
                    progress(received, total)


def extract_zip(zip_path: Path, dest_dir: Path) -> None:
    """Extract the release ZIP into ``dest_dir`` (created if missing)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest_dir)


SIDECAR_BAT_TEMPLATE = r"""@echo off
REM Champ Assistant in-app updater - waits for parent app to exit, swaps files,
REM relaunches, then self-deletes. Generated at runtime; do not commit to repo.
setlocal enabledelayedexpansion

set PARENT_PID=%~1
set STAGED_DIR=%~2
set INSTALL_DIR=%~3

echo [updater] waiting for app to close (pid %PARENT_PID%)...
:waitloop
tasklist /FI "PID eq %PARENT_PID%" 2>nul | find "%PARENT_PID%" >nul
if not errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto waitloop
)

REM Windows can hold the .exe file lock for ~1-2s after process exit while
REM the kernel releases handles. Give it a moment before touching files.
echo [updater] giving Windows 3s to release file handles...
timeout /t 3 /nobreak >nul

echo [updater] installing new version (with retry on file locks)...
REM robocopy is built into Windows since Vista. /MIR mirrors source to dest,
REM /R:5 retries 5 times on locked files, /W:2 waits 2s between retries,
REM /NFL/NDL/NJH/NJS keep output readable.
robocopy "%STAGED_DIR%" "%INSTALL_DIR%" /MIR /R:5 /W:2 /NFL /NDL /NJH /NJS
set RC=%errorlevel%
REM robocopy exit codes: 0-7 = success (0=no change, 1=files copied, etc.),
REM 8+ = real failure.
if %RC% GEQ 8 (
    echo.
    echo [updater] file swap failed (robocopy exit %RC%).
    echo [updater] Likely cause: another champ-assistant.exe is still running
    echo            or an antivirus is scanning the file.
    echo.
    echo [updater] Manual recovery:
    echo   1. Close all champ-assistant.exe processes ^(Task Manager^).
    echo   2. Copy contents of %STAGED_DIR% into %INSTALL_DIR%.
    echo.
    pause
    exit /b 1
)

echo [updater] starting new version...
start "" "%INSTALL_DIR%\__EXE__"

REM clean up staging + self-delete
rmdir /S /Q "%STAGED_DIR%" 2>nul
(goto) 2>nul & del "%~f0"
"""


def write_sidecar_bat(
    bat_path: Path,
    *,
    parent_pid: int,
    staged_dir: Path,
    install_directory: Path,
    exe_name: str = EXE_NAME,
) -> Path:
    """Render the sidecar swap script. Returns ``bat_path``."""
    body = SIDECAR_BAT_TEMPLATE.replace("__EXE__", exe_name)
    bat_path.parent.mkdir(parents=True, exist_ok=True)
    bat_path.write_text(body, encoding="ascii")
    # The bat will be invoked as: bat parent_pid staged_dir install_dir
    logger.info(
        "sidecar_written path=%s pid=%d staged=%s install=%s",
        bat_path, parent_pid, staged_dir, install_directory,
    )
    return bat_path


def launch_sidecar(
    bat_path: Path,
    *,
    parent_pid: int,
    staged_dir: Path,
    install_directory: Path,
) -> None:
    """Spawn the sidecar bat detached so it survives our exit."""
    creationflags = 0
    if sys.platform.startswith("win"):
        # CREATE_NEW_CONSOLE so the user sees progress; DETACHED would hide it.
        creationflags = (
            getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    subprocess.Popen(
        [str(bat_path), str(parent_pid), str(staged_dir), str(install_directory)],
        creationflags=creationflags,
        close_fds=True,
        cwd=str(install_directory),
    )


def install_dir_writable(target: Path) -> bool:
    """Check if we can actually write into the install directory.

    Avoids the "downloaded 47MB then realised the folder is in Program Files
    and we don't have admin" failure mode. Tries to create + delete a
    sentinel file; returns False on PermissionError.
    """
    try:
        sentinel = target / ".champ-assistant-writable-check"
        sentinel.write_bytes(b"")
        sentinel.unlink()
        return True
    except (OSError, PermissionError):
        return False


async def apply_update(
    tag: str,
    *,
    install_directory: Path,
    staging_root: Path | None = None,
    progress: Callable[[str], None] | None = None,
    repo: str = DEFAULT_REPO,
) -> None:
    """Download → extract → write sidecar → launch sidecar.

    Caller is responsible for quitting the app *after* this returns. Raises
    on any download/extract failure so the UI can surface an error.
    """
    def emit(msg: str) -> None:
        logger.info("update_progress: %s", msg)
        if progress is not None:
            progress(msg)

    # Pre-flight: refuse to start a 47-MB download if we already know the
    # install directory is read-only (Program Files, restricted ACLs, etc.).
    if not install_dir_writable(install_directory):
        raise PermissionError(
            f"Install-Ordner nicht beschreibbar: {install_directory}. "
            "Verschiebe die App in einen Nutzer-Ordner (z.B. Documents) "
            "und versuche es erneut."
        )

    base = staging_root or Path(os.environ.get("TEMP", "/tmp")) / "champ-assistant-update"
    base.mkdir(parents=True, exist_ok=True)
    zip_path = base / RELEASE_ASSET_NAME
    staged_dir = base / "staged"
    if staged_dir.exists():
        # leftovers from a prior aborted run
        import shutil
        shutil.rmtree(staged_dir, ignore_errors=True)

    emit(f"Lade {tag} herunter…")
    url = asset_download_url(tag, repo=repo)

    def _on_bytes(received: int, total: int | None) -> None:
        if total:
            pct = int(received * 100 / total)
            emit(f"Lade {tag} herunter… {pct}%")

    await download_release_zip(url, zip_path, progress=_on_bytes)

    emit("Entpacke…")
    extract_zip(zip_path, staged_dir)

    bat_path = base / "apply-update.bat"
    write_sidecar_bat(
        bat_path,
        parent_pid=os.getpid(),
        staged_dir=staged_dir,
        install_directory=install_directory,
    )

    emit("Starte Update…")
    launch_sidecar(
        bat_path,
        parent_pid=os.getpid(),
        staged_dir=staged_dir,
        install_directory=install_directory,
    )

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


def update_log_path() -> Path:
    """Where the bootstrap installer writes its diagnostic output."""
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ChampAssistant" / "logs" / "last-update.log"
    return Path.home() / ".champ-assistant" / "logs" / "last-update.log"


def launch_bootstrap_installer(
    staged_dir: Path,
    *,
    install_directory: Path,
    parent_pid: int,
    exe_name: str = EXE_NAME,
) -> None:
    """Launch the newly extracted exe in bootstrap mode so it installs itself.

    The staged exe starts immediately (while the current app is still alive),
    receives the old app's PID, waits for it to exit via WaitForSingleObject,
    then copies staged_dir → install_directory using shutil and relaunches from
    the install directory.

    This avoids all .bat / cmd.exe quoting issues — it's just a direct
    subprocess.Popen of a real exe that we already know works (CI validated it).
    """
    staged_exe = staged_dir / exe_name
    if not staged_exe.is_file():
        raise FileNotFoundError(f"Staged exe not found: {staged_exe}")

    creationflags = 0
    startupinfo = None
    if sys.platform.startswith("win"):
        creationflags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE

    log_path = update_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(
        "bootstrap_installer_launched staged=%s install=%s pid=%d",
        staged_dir, install_directory, parent_pid,
    )
    subprocess.Popen(
        [
            str(staged_exe),
            "--bootstrap-staged", str(staged_dir),
            "--bootstrap-install", str(install_directory),
            "--bootstrap-parent-pid", str(parent_pid),
        ],
        creationflags=creationflags,
        startupinfo=startupinfo,
        close_fds=True,
        cwd=str(staged_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def read_last_update_status() -> tuple[str, str] | None:
    """Inspect the previous update's log. Returns (verdict, last_line)
    or None if no log exists.

    Verdict is one of:
      - "ok"      successful relaunch
      - "fail"    bat failed at some step (robocopy / launch / verification)
      - "stale"   log exists but is older than 10 minutes (probably from a
                  prior session, not this start)
    """
    log_path = update_log_path()
    if not log_path.is_file():
        return None
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not text.strip():
        return None
    last = text.strip().splitlines()[-1]
    if "SUCCESS" in last:
        return ("ok", last)
    if "FAIL" in last:
        return ("fail", last)
    # Bat in progress or partial — treat as stale unless we know better.
    import time
    if (time.time() - log_path.stat().st_mtime) > 600:
        return ("stale", last)
    return ("ok", last)


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
    """Download → extract → launch bootstrap installer → (caller quits app).

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

    emit("Starte Update…")
    launch_bootstrap_installer(
        staged_dir,
        install_directory=install_directory,
        parent_pid=os.getpid(),
    )

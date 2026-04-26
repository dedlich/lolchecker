"""LCU lockfile parser + cross-platform path resolution.

The League client writes a lockfile on startup with the format::

    name:pid:port:password:protocol

Example: ``LeagueClient:23856:64144:abc123XYZ:https``

The file is short-lived (deleted on client exit) and only readable while the
client is running. We treat it as untrusted input — defensively parse, surface
clear errors, and *never* let the password reach a log.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


class LockfileError(Exception):
    """Base class for lockfile problems."""


class LockfileNotFound(LockfileError):
    """No lockfile exists at any candidate path (client likely not running)."""


class LockfileCorrupt(LockfileError):
    """Lockfile exists but cannot be parsed (mid-write, truncated, garbage)."""


@dataclass(frozen=True)
class LockfileInfo:
    process_name: str
    pid: int
    port: int
    password: str
    protocol: str

    def __repr__(self) -> str:
        # Mask the password — masterplan §4.5 / §7: never log credentials.
        return (
            f"LockfileInfo(process_name={self.process_name!r}, pid={self.pid}, "
            f"port={self.port}, password='***', protocol={self.protocol!r})"
        )

    @property
    def base_url(self) -> str:
        return f"{self.protocol}://127.0.0.1:{self.port}"

    @property
    def auth(self) -> tuple[str, str]:
        # LCU uses HTTP Basic with the literal username "riot".
        return ("riot", self.password)


def candidate_paths(
    *,
    platform: str | None = None,
    env: dict[str, str] | None = None,
    home: Path | None = None,
) -> list[Path]:
    """Ordered list of locations where the lockfile may live, per platform.

    Pure function — accepts the platform / env / home as parameters so tests
    can exercise Windows path logic on macOS and vice versa.
    """
    plat = platform if platform is not None else sys.platform
    e = env if env is not None else dict(os.environ)
    h = home if home is not None else Path.home()

    paths: list[Path] = []
    if plat.startswith("win"):
        local_app = e.get("LOCALAPPDATA")
        if local_app:
            paths.append(Path(local_app) / "Riot Games" / "League of Legends" / "lockfile")
        # Fallback when %LOCALAPPDATA% is unset (rare, but masterplan §4.2 mentions it).
        paths.append(h / "AppData" / "Local" / "Riot Games" / "League of Legends" / "lockfile")
        for pf_var in ("ProgramFiles", "ProgramFiles(x86)"):
            pf = e.get(pf_var)
            if pf:
                paths.append(Path(pf) / "Riot Games" / "League of Legends" / "lockfile")
    elif plat == "darwin":
        paths.append(Path("/Applications/League of Legends.app/Contents/LoL/lockfile"))
        paths.append(
            h / "Library" / "Application Support" / "Riot Games" / "League of Legends" / "lockfile"
        )
    else:
        # Linux is not officially supported but useful for CI / tests.
        paths.append(h / ".local" / "share" / "Riot Games" / "League of Legends" / "lockfile")

    # De-duplicate while preserving order.
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def find_lockfile(
    *,
    platform: str | None = None,
    env: dict[str, str] | None = None,
    home: Path | None = None,
    extra: list[Path] | None = None,
) -> Path:
    """Return the first existing lockfile path. Raises ``LockfileNotFound`` if none."""
    candidates = candidate_paths(platform=platform, env=env, home=home)
    if extra:
        candidates = list(extra) + candidates
    for p in candidates:
        if p.is_file():
            return p
    raise LockfileNotFound(
        "No lockfile found. Searched: " + ", ".join(str(p) for p in candidates)
    )


def parse_lockfile_text(text: str) -> LockfileInfo:
    """Parse the lockfile contents. Defensive — every failure is a ``LockfileCorrupt``."""
    stripped = text.strip()
    if not stripped:
        raise LockfileCorrupt("Lockfile is empty")

    parts = stripped.split(":")
    if len(parts) != 5:
        raise LockfileCorrupt(
            f"Expected 5 colon-separated fields, got {len(parts)}"
        )

    name, pid_s, port_s, password, protocol = parts
    if not name:
        raise LockfileCorrupt("Empty process name")
    try:
        pid = int(pid_s)
        port = int(port_s)
    except ValueError as exc:
        raise LockfileCorrupt(f"Non-numeric pid/port: {exc}") from exc
    if pid <= 0:
        raise LockfileCorrupt(f"PID must be positive, got {pid}")
    if not 0 < port < 65536:
        raise LockfileCorrupt(f"Port out of range: {port}")
    if not password:
        raise LockfileCorrupt("Empty password")
    if protocol not in ("http", "https"):
        raise LockfileCorrupt(f"Unexpected protocol: {protocol!r}")

    return LockfileInfo(
        process_name=name,
        pid=pid,
        port=port,
        password=password,
        protocol=protocol,
    )


def parse_lockfile(path: Path) -> LockfileInfo:
    """Read + parse a lockfile.

    Reads with UTF-8 and immediately closes the handle (Windows file-locking
    per masterplan §4.2 — keep the read window as short as possible).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise LockfileNotFound(str(path)) from exc
    except OSError as exc:
        raise LockfileError(f"Could not read {path}: {exc}") from exc
    return parse_lockfile_text(text)

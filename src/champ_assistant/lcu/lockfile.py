"""LCU lockfile parser.

Phase 2 module. Parses `lockfile` from %LOCALAPPDATA%\\Riot Games\\League of Legends\\
on Windows and the Mac equivalent. Format: name:pid:port:password:protocol.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LockfileInfo:
    process_name: str
    pid: int
    port: int
    password: str
    protocol: str

    def __repr__(self) -> str:
        return (
            f"LockfileInfo(process_name={self.process_name!r}, pid={self.pid}, "
            f"port={self.port}, password='***', protocol={self.protocol!r})"
        )


def find_lockfile_path() -> Path | None:
    raise NotImplementedError("Phase 2")


def parse_lockfile(path: Path) -> LockfileInfo:
    raise NotImplementedError("Phase 2")

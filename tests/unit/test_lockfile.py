"""Tests for the LCU lockfile parser + path resolution.

Coverage target per masterplan §5.2: 100%.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from champ_assistant.lcu.lockfile import (
    LockfileCorrupt,
    LockfileError,
    LockfileInfo,
    LockfileNotFound,
    candidate_paths,
    find_lockfile,
    find_lockfile_via_process,
    parse_lockfile,
    parse_lockfile_text,
)

VALID_TEXT = "LeagueClient:23856:64144:abc123XYZ:https"


# ---------------------------------------------------------------------------
# parse_lockfile_text — happy path
# ---------------------------------------------------------------------------

def test_parse_valid_lockfile_text() -> None:
    info = parse_lockfile_text(VALID_TEXT)
    assert info.process_name == "LeagueClient"
    assert info.pid == 23856
    assert info.port == 64144
    assert info.password == "abc123XYZ"
    assert info.protocol == "https"


def test_parse_strips_trailing_whitespace_and_newlines() -> None:
    info = parse_lockfile_text(VALID_TEXT + "\n\r\n  ")
    assert info.port == 64144


def test_parse_accepts_http_protocol() -> None:
    info = parse_lockfile_text("LeagueClient:1:2:pw:http")
    assert info.protocol == "http"


# ---------------------------------------------------------------------------
# parse_lockfile_text — error cases
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text,fragment",
    [
        ("", "empty"),
        ("   \n", "empty"),
        ("LeagueClient:1:2:pw", "5 colon-separated"),  # 4 fields
        ("LeagueClient:1:2:pw:https:extra", "5 colon-separated"),  # 6 fields
        (":1:2:pw:https", "Empty process name"),
        ("LeagueClient:abc:2:pw:https", "Non-numeric"),
        ("LeagueClient:1:abc:pw:https", "Non-numeric"),
        ("LeagueClient:0:2:pw:https", "PID must be positive"),
        ("LeagueClient:1:0:pw:https", "Port out of range"),
        ("LeagueClient:1:99999:pw:https", "Port out of range"),
        ("LeagueClient:1:-5:pw:https", "Port out of range"),
        ("LeagueClient:1:2::https", "Empty password"),
        ("LeagueClient:1:2:pw:ftp", "Unexpected protocol"),
    ],
)
def test_parse_invalid_text_raises_corrupt(text: str, fragment: str) -> None:
    with pytest.raises(LockfileCorrupt) as excinfo:
        parse_lockfile_text(text)
    assert fragment.lower() in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# LockfileInfo
# ---------------------------------------------------------------------------

def test_repr_masks_password() -> None:
    info = LockfileInfo("LeagueClient", 1, 2, "supersecret", "https")
    rep = repr(info)
    assert "supersecret" not in rep
    assert "***" in rep


def test_str_does_not_leak_password() -> None:
    info = LockfileInfo("LeagueClient", 1, 2, "supersecret", "https")
    # str() falls back to __repr__ for dataclass(frozen=True)
    assert "supersecret" not in str(info)


def test_base_url_and_auth_helpers() -> None:
    info = parse_lockfile_text(VALID_TEXT)
    assert info.base_url == "https://127.0.0.1:64144"
    assert info.auth == ("riot", "abc123XYZ")


# ---------------------------------------------------------------------------
# parse_lockfile (filesystem)
# ---------------------------------------------------------------------------

def test_parse_lockfile_reads_file(tmp_path: Path) -> None:
    f = tmp_path / "lockfile"
    f.write_text(VALID_TEXT, encoding="utf-8")
    info = parse_lockfile(f)
    assert info.port == 64144


def test_parse_lockfile_missing_raises_not_found(tmp_path: Path) -> None:
    with pytest.raises(LockfileNotFound):
        parse_lockfile(tmp_path / "nonexistent")


def test_parse_lockfile_corrupt_raises(tmp_path: Path) -> None:
    f = tmp_path / "lockfile"
    f.write_text("garbage", encoding="utf-8")
    with pytest.raises(LockfileCorrupt):
        parse_lockfile(f)


def test_parse_lockfile_unicode_path(tmp_path: Path) -> None:
    """Windows users with names like 'Müller' must work — masterplan §4.2."""
    weird_dir = tmp_path / "Üser_Mügen_λ" / "Riot Games" / "League of Legends"
    weird_dir.mkdir(parents=True)
    f = weird_dir / "lockfile"
    f.write_text(VALID_TEXT, encoding="utf-8")
    info = parse_lockfile(f)
    assert info.password == "abc123XYZ"


def test_parse_lockfile_directory_raises_lockfile_error(tmp_path: Path) -> None:
    """Reading a directory path should surface as LockfileError, not crash."""
    with pytest.raises(LockfileError):
        parse_lockfile(tmp_path)


# ---------------------------------------------------------------------------
# candidate_paths — platform-aware
# ---------------------------------------------------------------------------

def test_candidates_windows_uses_localappdata() -> None:
    paths = candidate_paths(
        platform="win32",
        env={"LOCALAPPDATA": r"C:\Users\Dennis\AppData\Local"},
        home=Path(r"C:\Users\Dennis"),
    )
    # First candidate is Riot's modern default install directory.
    assert paths[0] == Path("C:") / "Riot Games" / "League of Legends" / "lockfile"
    # %LOCALAPPDATA% candidate must also be present.
    assert any("Local" in str(p) and "Riot Games" in str(p) for p in paths)
    assert all(p.name == "lockfile" for p in paths)


def test_candidates_windows_includes_riot_games_root() -> None:
    """Riot's modern installer lands at C:\\Riot Games\\... — must be searched."""
    paths = candidate_paths(platform="win32", env={}, home=Path(r"C:\Users\X"))
    expected = Path("C:") / "Riot Games" / "League of Legends" / "lockfile"
    assert expected in paths


def test_candidates_windows_falls_back_when_no_localappdata() -> None:
    paths = candidate_paths(
        platform="win32",
        env={},
        home=Path(r"C:\Users\Dennis"),
    )
    # Without LOCALAPPDATA we still get a home-based fallback
    assert any("AppData" in str(p) for p in paths)


def test_candidates_windows_includes_program_files() -> None:
    paths = candidate_paths(
        platform="win32",
        env={
            "LOCALAPPDATA": r"C:\Users\Dennis\AppData\Local",
            "ProgramFiles": r"C:\Program Files",
            "ProgramFiles(x86)": r"C:\Program Files (x86)",
        },
        home=Path(r"C:\Users\Dennis"),
    )
    assert any("Program Files" in str(p) for p in paths)


def test_candidates_macos() -> None:
    paths = candidate_paths(platform="darwin", env={}, home=Path("/Users/me"))
    assert paths[0] == Path("/Applications/League of Legends.app/Contents/LoL/lockfile")
    # as_posix() — str(Path(...)) uses backslashes on Windows runners, but the
    # darwin path layout is logically the same regardless of host OS.
    assert any("Library/Application Support" in p.as_posix() for p in paths)


def test_candidates_linux_fallback() -> None:
    paths = candidate_paths(platform="linux", env={}, home=Path("/home/me"))
    assert len(paths) >= 1
    assert ".local" in str(paths[0])


def test_candidates_dedupes() -> None:
    # On Windows, if home and LOCALAPPDATA produce overlapping paths they should dedup.
    paths = candidate_paths(
        platform="win32",
        env={"LOCALAPPDATA": r"C:\Users\Dennis\AppData\Local"},
        home=Path(r"C:\Users\Dennis"),
    )
    assert len(set(paths)) == len(paths)


# ---------------------------------------------------------------------------
# find_lockfile
# ---------------------------------------------------------------------------

def test_find_lockfile_returns_first_existing(tmp_path: Path) -> None:
    a = tmp_path / "a-lockfile"
    b = tmp_path / "b-lockfile"
    b.write_text(VALID_TEXT, encoding="utf-8")
    found = find_lockfile(platform="darwin", env={}, home=tmp_path, extra=[a, b])
    assert found == b


def test_find_lockfile_raises_when_none_exist(tmp_path: Path) -> None:
    with pytest.raises(LockfileNotFound) as excinfo:
        find_lockfile(platform="darwin", env={}, home=tmp_path)
    # Error message should list searched paths so users can debug.
    assert "Searched" in str(excinfo.value)


def test_find_lockfile_extra_paths_take_priority(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    primary.write_text(VALID_TEXT, encoding="utf-8")

    fallback_dir = tmp_path / "Library" / "Application Support" / "Riot Games" / "League of Legends"
    fallback_dir.mkdir(parents=True)
    (fallback_dir / "lockfile").write_text(VALID_TEXT, encoding="utf-8")

    found = find_lockfile(platform="darwin", env={}, home=tmp_path, extra=[primary])
    assert found == primary


# ---------------------------------------------------------------------------
# Process-based discovery (psutil fallback)
# ---------------------------------------------------------------------------

class _FakeProc:
    def __init__(self, **info: object) -> None:
        self.info = info


def test_find_via_process_returns_lockfile_from_cwd(tmp_path: Path) -> None:
    install = tmp_path / "RiotGames" / "League of Legends"
    install.mkdir(parents=True)
    (install / "lockfile").write_text(VALID_TEXT, encoding="utf-8")

    procs = [
        _FakeProc(name="explorer.exe", cwd="C:\\Windows"),
        _FakeProc(name="LeagueClient.exe", cwd=str(install)),
    ]
    found = find_lockfile_via_process(process_iter=lambda: procs)
    assert found == install / "lockfile"


def test_find_via_process_falls_back_to_exe_dir(tmp_path: Path) -> None:
    install = tmp_path / "alt" / "League of Legends"
    install.mkdir(parents=True)
    (install / "lockfile").write_text(VALID_TEXT, encoding="utf-8")

    procs = [
        _FakeProc(
            name="LeagueClientUx.exe",
            cwd=None,
            exe=str(install / "LeagueClientUx.exe"),
        )
    ]
    found = find_lockfile_via_process(process_iter=lambda: procs)
    assert found == install / "lockfile"


def test_find_via_process_returns_none_when_no_league() -> None:
    procs = [
        _FakeProc(name="chrome.exe", cwd="C:\\"),
        _FakeProc(name="explorer.exe", cwd="C:\\Windows"),
    ]
    assert find_lockfile_via_process(process_iter=lambda: procs) is None


def test_find_via_process_skips_lockfile_that_does_not_exist(tmp_path: Path) -> None:
    procs = [_FakeProc(name="LeagueClient.exe", cwd=str(tmp_path / "nope"))]
    assert find_lockfile_via_process(process_iter=lambda: procs) is None


def test_find_via_process_swallows_per_proc_errors(tmp_path: Path) -> None:
    install = tmp_path / "x"
    install.mkdir()
    (install / "lockfile").write_text(VALID_TEXT, encoding="utf-8")

    class _Broken:
        @property
        def info(self) -> dict[str, object]:
            raise RuntimeError("psutil access denied")

    procs = [
        _Broken(),
        _FakeProc(name="LeagueClient.exe", cwd=str(install)),
    ]
    found = find_lockfile_via_process(process_iter=lambda: procs)
    assert found == install / "lockfile"


def test_find_lockfile_falls_back_to_process(tmp_path: Path) -> None:
    """If candidate_paths come up empty, the process scan still finds it."""
    install = tmp_path / "LoL"
    install.mkdir()
    lockfile = install / "lockfile"
    lockfile.write_text(VALID_TEXT, encoding="utf-8")

    procs = [_FakeProc(name="LeagueClient.exe", cwd=str(install))]
    found = find_lockfile(
        platform="darwin",
        env={},
        home=tmp_path,
        process_iter=lambda: procs,
    )
    assert found == lockfile


def test_find_lockfile_candidate_path_wins_over_process(tmp_path: Path) -> None:
    """If a candidate path exists, we use that and skip the process scan."""
    primary = tmp_path / "primary"
    primary.write_text(VALID_TEXT, encoding="utf-8")

    install = tmp_path / "via_proc"
    install.mkdir()
    (install / "lockfile").write_text(VALID_TEXT, encoding="utf-8")

    called = {"count": 0}

    def _spy() -> list[_FakeProc]:
        called["count"] += 1
        return [_FakeProc(name="LeagueClient.exe", cwd=str(install))]

    found = find_lockfile(
        platform="darwin", env={}, home=tmp_path, extra=[primary], process_iter=_spy
    )
    assert found == primary
    assert called["count"] == 0  # process scan never invoked

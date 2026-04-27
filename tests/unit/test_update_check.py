"""Tests for the GitHub Releases update checker."""
from __future__ import annotations

import zipfile
from pathlib import Path

import httpx
import pytest
import respx

from champ_assistant.update_check import (
    apply_update,
    asset_download_url,
    check_for_update,
    download_release_zip,
    extract_zip,
    fetch_latest_release,
    is_newer,
    write_sidecar_bat,
)

LATEST_URL = "https://api.github.com/repos/dedlich/lolchecker/releases/latest"


@pytest.mark.parametrize(
    "latest,current,expected",
    [
        ("v0.2.0", "0.1.0", True),
        ("v0.2.0", "0.2.0", False),
        ("v0.1.5", "0.2.0", False),
        ("0.2.0", "0.1.99", True),
        ("v1.0.0-beta.1", "0.9.0", True),
        ("v0.1.0", "0.1.0-beta.1", False),  # 0.1.0 == 0.1.0
    ],
)
def test_is_newer(latest: str, current: str, expected: bool) -> None:
    assert is_newer(latest, current) is expected


@pytest.mark.asyncio
@respx.mock
async def test_fetch_latest_release_returns_tag_and_url() -> None:
    respx.get(LATEST_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "tag_name": "v0.2.0",
                "html_url": "https://github.com/dedlich/lolchecker/releases/tag/v0.2.0",
                "name": "v0.2.0",
            },
        )
    )
    info = await fetch_latest_release()
    assert info == {
        "tag": "v0.2.0",
        "url": "https://github.com/dedlich/lolchecker/releases/tag/v0.2.0",
    }


@pytest.mark.asyncio
@respx.mock
async def test_fetch_latest_release_returns_none_on_404() -> None:
    respx.get(LATEST_URL).mock(return_value=httpx.Response(404))
    assert await fetch_latest_release() is None


@pytest.mark.asyncio
@respx.mock
async def test_fetch_latest_release_returns_none_on_network_error() -> None:
    respx.get(LATEST_URL).mock(side_effect=httpx.ConnectError("offline"))
    assert await fetch_latest_release() is None


@pytest.mark.asyncio
@respx.mock
async def test_check_for_update_returns_info_when_newer() -> None:
    respx.get(LATEST_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "tag_name": "v0.3.0",
                "html_url": "https://github.com/dedlich/lolchecker/releases/tag/v0.3.0",
            },
        )
    )
    info = await check_for_update("0.1.0")
    assert info is not None
    assert info["tag"] == "v0.3.0"


@pytest.mark.asyncio
@respx.mock
async def test_check_for_update_returns_none_when_same() -> None:
    respx.get(LATEST_URL).mock(
        return_value=httpx.Response(
            200, json={"tag_name": "v0.1.0", "html_url": "https://example.com"}
        )
    )
    assert await check_for_update("0.1.0") is None


@pytest.mark.asyncio
@respx.mock
async def test_check_for_update_returns_none_on_failure() -> None:
    respx.get(LATEST_URL).mock(return_value=httpx.Response(500))
    assert await check_for_update("0.1.0") is None


def test_asset_download_url_format() -> None:
    url = asset_download_url("v0.7.0")
    assert url == (
        "https://github.com/dedlich/lolchecker/releases/download/"
        "v0.7.0/champ-assistant-windows.zip"
    )


@pytest.mark.asyncio
@respx.mock
async def test_download_release_zip_writes_file(tmp_path: Path) -> None:
    payload = b"fake-zip-bytes-x" * 1024
    respx.get("https://example.test/release.zip").mock(
        return_value=httpx.Response(200, content=payload)
    )
    dest = tmp_path / "release.zip"
    captured: list[tuple[int, int | None]] = []
    await download_release_zip(
        "https://example.test/release.zip",
        dest,
        progress=lambda r, t: captured.append((r, t)),
    )
    assert dest.read_bytes() == payload
    assert captured  # progress callback fired at least once


@pytest.mark.asyncio
@respx.mock
async def test_download_release_zip_raises_on_404(tmp_path: Path) -> None:
    respx.get("https://example.test/release.zip").mock(
        return_value=httpx.Response(404)
    )
    with pytest.raises(httpx.HTTPError):
        await download_release_zip(
            "https://example.test/release.zip", tmp_path / "release.zip"
        )


def test_extract_zip_unpacks_contents(tmp_path: Path) -> None:
    zp = tmp_path / "release.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("champ-assistant.exe", b"exe-bytes")
        zf.writestr("_internal/data/counters.json", b"{}")
    out = tmp_path / "staged"
    extract_zip(zp, out)
    assert (out / "champ-assistant.exe").read_bytes() == b"exe-bytes"
    assert (out / "_internal" / "data" / "counters.json").read_bytes() == b"{}"


def test_write_sidecar_bat_renders_template(tmp_path: Path) -> None:
    bat = tmp_path / "apply-update.bat"
    write_sidecar_bat(
        bat,
        parent_pid=12345,
        staged_dir=tmp_path / "staged",
        install_directory=tmp_path / "install",
        exe_name="champ-assistant.exe",
    )
    body = bat.read_text(encoding="ascii")
    # Sidecar must wait for the parent exe, swap files via xcopy, relaunch,
    # and self-delete. Verify the structural markers are present rather than
    # asserting on whitespace.
    assert "tasklist" in body
    assert "xcopy" in body
    assert 'start "" "%INSTALL_DIR%\\champ-assistant.exe"' in body
    assert "del \"%~f0\"" in body


@pytest.mark.asyncio
@respx.mock
async def test_apply_update_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: builds a fake release zip, serves it, runs apply_update.

    Stubs out launch_sidecar so the test doesn't actually spawn a process.
    Verifies the staged dir + bat were written correctly.
    """
    # Build a fake release zip in memory
    fake_release = tmp_path / "fake-release.zip"
    with zipfile.ZipFile(fake_release, "w") as zf:
        zf.writestr("champ-assistant.exe", b"new-exe-bytes")
    payload = fake_release.read_bytes()

    tag = "v9.9.9"
    expected_url = asset_download_url(tag)
    respx.get(expected_url).mock(return_value=httpx.Response(200, content=payload))

    install = tmp_path / "install"
    install.mkdir()
    staging = tmp_path / "staging"
    progress_msgs: list[str] = []
    spawned: list[tuple[Path, int, Path, Path]] = []

    def fake_launch(bat_path: Path, **kwargs: object) -> None:
        spawned.append(
            (
                bat_path,
                kwargs["parent_pid"],  # type: ignore[arg-type]
                kwargs["staged_dir"],  # type: ignore[arg-type]
                kwargs["install_directory"],  # type: ignore[arg-type]
            )
        )

    monkeypatch.setattr(
        "champ_assistant.update_check.launch_sidecar", fake_launch
    )

    await apply_update(
        tag,
        install_directory=install,
        staging_root=staging,
        progress=progress_msgs.append,
    )

    # Verify staging contains extracted contents and bat was rendered.
    assert (staging / "staged" / "champ-assistant.exe").read_bytes() == b"new-exe-bytes"
    assert (staging / "apply-update.bat").exists()
    assert spawned and spawned[0][0] == staging / "apply-update.bat"
    assert progress_msgs  # user-facing progress fired

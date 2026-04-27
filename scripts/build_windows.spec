# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Windows champ-assistant build.

Produces a one-folder bundle under ``dist/champ-assistant/``. We deliberately
choose one-folder over one-file because:
  - Antivirus heuristics flag PyInstaller one-file binaries far more often
    than one-folder bundles (masterplan §13 risk: AV flags).
  - Qt's plugin discovery is more reliable from a regular folder.
  - Startup is faster (no temp extraction on every launch).

Run from the repo root:
    pyinstaller scripts/build_windows.spec --noconfirm --clean
"""
from pathlib import Path

# PyInstaller injects SPECPATH at exec time; it points at this .spec file.
ROOT = Path(SPECPATH).resolve().parent  # repo root

block_cipher = None


a = Analysis(
    [str(ROOT / "src" / "champ_assistant" / "__main__.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[
        # Static data — counters / tier list / tags read at runtime.
        (str(ROOT / "data" / "counters.json"), "data"),
        (str(ROOT / "data" / "tiers.json"), "data"),
        (str(ROOT / "data" / "tags.json"), "data"),
        (str(ROOT / "data" / "builds.json"), "data"),
        # NB: scripts/update.bat is NOT bundled here — PyInstaller datas
        # land under _internal/ in one-folder mode, not next to the exe.
        # The build workflow copies update.bat into dist/champ-assistant/
        # alongside champ-assistant.exe so users can click it.
        # Demo fixtures so --dry-run works in the packaged exe (default
        # FIXTURE_DIR resolves under _MEIPASS/tests/fixtures/sessions).
        # Skip 12_corrupt.json — it's a parser-robustness test asset, not
        # a usable session.
        (str(ROOT / "tests" / "fixtures" / "sessions" / "01_ban_phase.json"),
         "tests/fixtures/sessions"),
        (str(ROOT / "tests" / "fixtures" / "sessions" / "02_early_picks.json"),
         "tests/fixtures/sessions"),
        (str(ROOT / "tests" / "fixtures" / "sessions" / "04_my_turn_top.json"),
         "tests/fixtures/sessions"),
    ],
    hiddenimports=[
        # Subpackages PyInstaller may not auto-detect from string-based imports.
        "champ_assistant",
        "champ_assistant.app",
        "champ_assistant.config",
        "champ_assistant.safety",
        "champ_assistant.tasks",
        "champ_assistant.lcu",
        "champ_assistant.lcu.lockfile",
        "champ_assistant.lcu.client",
        "champ_assistant.lcu.events",
        "champ_assistant.lcu.sources",
        "champ_assistant.data",
        "champ_assistant.data.models",
        "champ_assistant.data.loader",
        "champ_assistant.data.datadragon",
        "champ_assistant.advisor",
        "champ_assistant.advisor.counters",
        "champ_assistant.advisor.composition",
        "champ_assistant.advisor.picks",
        "champ_assistant.advisor.claude",
        "champ_assistant.data.runtime_counters",
        "champ_assistant.update_check",
        "dotenv",
        "champ_assistant.ui",
        "champ_assistant.ui.overlay",
        "champ_assistant.ui.enemy_row",
        "champ_assistant.ui.pick_card",
        "champ_assistant.ui.styles",
        "champ_assistant.ui.widgets",
        "champ_assistant.ui.view_model",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Test deps — never needed at runtime.
        "pytest",
        "pytest_asyncio",
        "pytest_qt",
        "pytest_timeout",
        "respx",
        "hypothesis",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="champ-assistant",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # GUI app — no console window flashes on launch
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="champ-assistant",
)

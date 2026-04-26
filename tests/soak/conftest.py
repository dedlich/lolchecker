"""pytest CLI options for the soak suite.

Lives in conftest.py so options are registered when pytest collects the
soak directory specifically (or the whole tree). Test bodies read them
via ``request.config.getoption(...)``.
"""
from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--soak-duration",
        type=int,
        default=60,
        help="Soak test duration in seconds (default 60; masterplan target 14400 = 4h).",
    )
    parser.addoption(
        "--soak-rss-cap",
        type=float,
        default=10.0,
        help="Maximum allowed RSS growth in MB (default 10 per masterplan §5.7).",
    )

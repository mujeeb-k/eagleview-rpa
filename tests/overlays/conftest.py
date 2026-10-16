from pathlib import Path

import pytest
from playwright.async_api import Browser

from tests.support.replay import Replay, make_replayer


@pytest.fixture
def replay(browser: Browser, tmp_path: Path) -> Replay:
    return make_replayer(browser, tmp_path / "runs")

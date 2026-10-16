"""Invariant 4's one exception stays one: video is off unless the demo video script turns it on.

Every API that can record defaults to no video, the library only passes the setting through,
and the only caller that sets it is `scripts/evidence/demo_video.py`."""

import inspect
import re
from pathlib import Path

from eagleview.catalog.runner import Catalog
from eagleview.compiler.pipeline import discover_and_compile
from eagleview.discovery.agent_loop import discover
from eagleview.runner import run_replay
from eagleview.surfaces.web.launch import surface_opener
from eagleview.surfaces.web.playwright_surface import PlaywrightSurface

REPO = Path(__file__).resolve().parents[1]
PASS_THROUGH = re.compile(r"record_video_dir=(record_video_dir|self\.record_video_dir)\b")


def test_every_recording_api_defaults_to_no_video() -> None:
    for fn in (run_replay, discover, discover_and_compile, surface_opener, PlaywrightSurface.open):
        assert inspect.signature(fn).parameters["record_video_dir"].default is None, fn
    assert Catalog.__dataclass_fields__["record_video_dir"].default is None


def test_only_the_demo_video_script_turns_video_on() -> None:
    setters: list[str] = []
    for path in sorted([*REPO.glob("src/**/*.py"), *REPO.glob("scripts/**/*.py")]):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "record_video_dir=" not in line or PASS_THROUGH.search(line):
                continue
            if '"record_video_dir":' in line or "record_video_dir: Path" in line:
                continue
            setters.append(f"{path.relative_to(REPO)}:{n}")
    assert setters, "the demo video script should set it"
    assert all(s.startswith("scripts/evidence/demo_video.py:") for s in setters), setters

"""The fixed documentation paths: REPORT.md uses PLAN §9's seven headings, exactly and in order."""

import re

from tests.fixtures import REPO

HEADINGS = [
    "Architecture",
    "Artifact schema",
    "Determinism & error handling",
    "Heterogeneity & multi-tenant",
    "Escalation & handoff",
    "Safety",
    "Cuts",
]


def test_report_uses_the_seven_headings_exactly() -> None:
    text = (REPO / "REPORT.md").read_text(encoding="utf-8")
    assert re.findall(r"^## (.+)$", text, flags=re.MULTILINE) == HEADINGS


def test_report_stays_within_three_pages() -> None:
    words = len((REPO / "REPORT.md").read_text(encoding="utf-8").split())
    assert words <= 1600, f"{words} words is more than three pages"


def test_the_fixed_paths_exist() -> None:
    for path in ("README.md", "REPORT.md", "evidence/README.md"):
        assert (REPO / path).is_file(), path

"""Discovery and the compiler write drafts only, and never replace a file that exists."""

from pathlib import Path

from eagleview.artifacts.loader import write_draft
from eagleview.artifacts.yaml_io import load_capability, load_model
from eagleview.schema.capability import Capability
from tests.fixtures import CAPABILITY


def draft() -> Capability:
    cap = load_model(CAPABILITY.read_text(), Capability)
    return Capability.model_validate(cap.model_dump() | {"status": "draft", "provenance": {}})


def test_an_existing_draft_is_never_replaced(tmp_path: Path) -> None:
    first = write_draft(draft(), tmp_path)
    original = first.read_text()
    second = write_draft(draft().model_copy(update={"title": "Edited title"}), tmp_path)
    third = write_draft(draft(), tmp_path)
    assert [p.name for p in (first, second, third)] == [
        "member.lookup_share_balances.yaml",
        "member.lookup_share_balances.2.yaml",
        "member.lookup_share_balances.3.yaml",
    ]
    assert first.read_text() == original
    assert load_capability(second).title == "Edited title"
    assert load_capability(third).provenance.content_hash is not None

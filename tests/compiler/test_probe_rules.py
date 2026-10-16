"""The rules around probing that need no browser: refusals, fallback probes, regex checks."""

from types import SimpleNamespace
from typing import Any

import pytest

from eagleview.artifacts.loader import load_for_run
from eagleview.compiler.probe import classify, fallback_probes
from eagleview.discovery.client import StructuredRefused, structured
from eagleview.discovery.meter import MemoryMeter
from tests.fixtures import CAPABILITY


def reply(stop_reason: str, content: list[Any]) -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=content,
        stop_details=SimpleNamespace(category="cyber"),
        model="claude-sonnet-5-5",
        usage=SimpleNamespace(input_tokens=500, output_tokens=0),
    )


def test_a_declined_or_empty_structured_reply_is_a_typed_error() -> None:
    with pytest.raises(StructuredRefused, match="declined"):
        structured(reply("refusal", []), "probe proposal")
    with pytest.raises(StructuredRefused, match="no output"):
        structured(reply("end_turn", []), "naming")
    text = SimpleNamespace(type="text", text='{"probes": []}')
    assert structured(reply("end_turn", [text]), "x") == {"probes": []}


def test_fallback_probes_keep_the_shape_and_pass_the_schema() -> None:
    cap = load_for_run(CAPABILITY).capability  # member_id pattern ^[0-9]{4,8}$
    assert fallback_probes(cap, {"member_id": "10482"}) == [
        {"member_id": "99999"},
        {"member_id": "00000"},
    ]


class _Client:
    def __init__(self, answer: dict[str, Any]) -> None:
        import json

        self.messages = SimpleNamespace(create=self._create)
        self._text = json.dumps(answer)

    async def _create(self, **_: Any) -> SimpleNamespace:
        return reply("end_turn", [SimpleNamespace(type="text", text=self._text)])


STATE = {
    "step_id": "submit_search",
    "texts": [{"text": "IBT-1004 NO MEMBER FOUND FOR NUMBER [member_id]", "frame": "main"}],
}


@pytest.mark.parametrize(
    ("pattern", "accepted"),
    [
        ("(?i)no member(s)? found", True),
        ("(?i)no member found for number \\[member_id\\]", False),  # depends on the input
        ("(?i)no member found for number 99999", False),  # the probe value itself
        ("(?i)nothing like this", False),  # does not match the screen
        ("(unclosed", False),
    ],
)
async def test_classified_patterns_must_be_input_independent_and_match(
    pattern: str, accepted: bool
) -> None:
    answer = {
        "business_outcome": True,
        "code": "MEMBER_NOT_FOUND",
        "message": "No member exists with that number.",
        "pattern": pattern,
        "frame": "main",
    }
    outcome, why = await classify(
        _Client(answer),  # type: ignore[arg-type]
        "claude-sonnet-5-5",
        MemoryMeter(),
        "r_x",
        STATE,
        {"member_id": "99999"},
    )
    assert (outcome is not None) == accepted, why
    if outcome is not None:
        assert outcome.source == "probed"

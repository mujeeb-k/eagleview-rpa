"""Segmenting and pruning on hand-made traces: dead ends go, values stay, choices bind to inputs."""

from typing import Any

import pytest

from eagleview.compiler.prune import prune
from eagleview.compiler.segment import CompileError, segment

VALUES = {"member_id": "10482", "share_type": "Money Market"}
_seq = iter(range(1, 10_000))


def rec(tool: str, before: str, after: str, **kw: Any) -> dict[str, Any]:
    changed = kw.pop("changed", before != after)
    return {
        "seq": next(_seq),
        "tool": tool,
        "status": kw.pop("status", "done"),
        "actor": kw.pop("actor", "llm"),
        "fingerprint": {"before": before, "after": after},
        "changed": changed,
        "input": kw.pop("input", {}),
        **kw,
    }


def link(name: str) -> dict[str, Any]:
    return {
        "tag": "a",
        "role": "link",
        "name": name,
        "frame": ["nav"],
        "candidates": [{"strategy": "text", "text": name, "exact": True}],
    }


FIELD = {
    "tag": "input",
    "role": "textbox",
    "input_type": "text",
    "frame": ["main"],
    "candidates": [{"strategy": "css", "selector": "input[name='txtA']"}],
}
SELECT = {
    "tag": "select",
    "role": "combobox",
    "frame": ["main"],
    "candidates": [{"strategy": "css", "selector": "select[name='f3']"}],
}
CHECK = {
    "tag": "input",
    "role": "checkbox",
    "input_type": "checkbox",
    "frame": ["main"],
    "candidates": [{"strategy": "css", "selector": "input[name='ack']"}],
}
GO = {
    "tag": "img",
    "role": "img",
    "frame": ["main"],
    "candidates": [{"strategy": "css", "selector": "img[src*='go']"}],
}
DONE = rec("declare_success", "D", "D", grounded={"text": "Member Detail", "frame": ["main"]})


def test_looking_and_retyping_are_dropped() -> None:
    records = [
        rec("left_click", "T", "S", grounded=link("Member Inquiry")),
        rec("screenshot", "S", "S"),
        rec("left_click", "S", "S", grounded=FIELD),
        rec("type", "S", "S", input={"text": "10483"}, changed=True),  # a typo
        rec("left_click", "S", "S", grounded=FIELD),
        rec("fill_input", "S", "S", bound="member_id", changed=True),
        rec("left_click", "S", "D", grounded=GO),
        DONE,
    ]
    kept = prune(segment(records, VALUES).steps)
    assert [s.kind for s in kept] == ["click", "fill", "click"]
    assert kept[1].value == "{{ inputs.member_id }}"  # the last fill in that state wins


def test_a_detour_that_comes_back_is_removed() -> None:
    records = [
        rec("left_click", "H", "S", grounded=link("Member Inquiry")),
        rec("left_click", "S", "T", grounded=link("Teller Totals")),  # detour
        rec("left_click", "T", "S", grounded=link("Member Inquiry")),  # back
        rec("left_click", "S", "S", grounded=FIELD),
        rec("fill_input", "S", "S", bound="member_id", changed=True),
        rec("left_click", "S", "D", grounded=GO),
        DONE,
    ]
    kept = prune(segment(records, VALUES).steps)
    assert [(s.kind, (s.grounded or {}).get("name")) for s in kept] == [
        ("click", "Member Inquiry"),
        ("fill", None),
        ("click", None),
    ]


def test_selects_bind_to_inputs_and_checkboxes_survive() -> None:
    records = [
        rec("left_click", "O", "O", grounded=SELECT, select_value="-- Select --"),
        rec("key", "O", "O", input={"text": "Down"}, select_value="Share Draft"),
        rec("key", "O", "O", input={"text": "Down"}, select_value="Money Market"),
        rec("left_click", "O", "O", grounded=CHECK),
        rec("left_click", "O", "R", grounded={**link("Validate"), "frame": ["main"]}),
        DONE,
    ]
    kept = prune(segment(records, VALUES).steps)
    assert [s.kind for s in kept] == ["select", "check", "click"]
    assert kept[0].option == "{{ inputs.share_type }}"


def test_redaction_placeholders_bind_too() -> None:
    records = [
        rec("left_click", "S", "S", grounded=FIELD),
        rec("type", "S", "S", input={"text": "[member_id]"}, changed=True),
        rec("left_click", "S", "D", grounded=GO),
        DONE,
    ]
    kept = prune(segment(records, VALUES).steps)
    assert kept[0].value == "{{ inputs.member_id }}"


def test_a_person_s_irreversible_click_is_a_step() -> None:
    records = [
        rec(
            "left_click",
            "R",
            "R",
            grounded={**link("Post"), "frame": ["main"]},
            status="handed_over",
            decision={"irreversible": True},
        ),
        rec(
            "left_click",
            "R",
            "P",
            grounded={**link("Post"), "frame": ["main"]},
            actor="operator:scripted",
            decision={"irreversible": True, "audit": True},
        ),
        DONE,
    ]
    kept = prune(segment(records, VALUES).steps)
    assert [(s.actor, s.irreversible) for s in kept] == [("operator:scripted", True)]


def test_a_trace_without_success_does_not_compile() -> None:
    with pytest.raises(CompileError, match="declare_success"):
        segment([rec("left_click", "H", "S", grounded=link("Member Inquiry"))], VALUES)


def test_a_declared_input_typed_into_a_select_binds_by_provenance() -> None:
    records = [
        rec("left_click", "O", "O", grounded=SELECT, select_value="-- Select --"),
        # The list is still open, so the value read now is the old one.
        rec("fill_input", "O", "O", bound="share_type", select_value="-- Select --"),
        rec("left_click", "O", "R", grounded={**link("Validate"), "frame": ["main"]}),
        DONE,
    ]
    kept = prune(segment(records, VALUES).steps)
    assert kept[0].kind == "select" and kept[0].option == "{{ inputs.share_type }}"


def test_a_key_that_submits_from_a_select_is_its_own_step() -> None:
    records = [
        rec("left_click", "O", "O", grounded=SELECT, select_value="-- Select --"),
        rec("fill_input", "O", "O", bound="share_type"),
        rec("key", "O", "R", input={"text": "Return"}),  # Enter submitted the form
        DONE,
    ]
    kept = prune(segment(records, VALUES).steps)
    assert [s.kind for s in kept] == ["select", "press"]

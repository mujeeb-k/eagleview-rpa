"""The capability model: the PLAN §3.2 example as a golden fixture, YAML round trips, the content
hash, and every consistency rule."""

import copy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from eagleview.artifacts.yaml_io import (
    ArtifactHashMismatch,
    dump_capability,
    dump_model,
    load_capability,
    load_model,
    load_yaml,
)
from eagleview.schema.capability import Capability, TableExtract
from eagleview.schema.hashing import content_hash
from eagleview.schema.predicates import AllOf, AnyOf, Not, OutputPresent, TextMatches, TextPresent
from tests.fixtures import CAPABILITY

PINNED_HASH = "sha256:b19625b0c603893652cf637a79ac575989576fa808de3d3d7bcd1678d608a6cb"


def fixture_data() -> dict[str, Any]:
    data = load_yaml(CAPABILITY.read_text())
    assert isinstance(data, dict)
    return data  # pyright: ignore[reportUnknownVariableType]


def draft(data: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(data)
    out["status"] = "draft"
    out["provenance"] = {}
    return out


def test_plan_example_loads_with_its_quirks() -> None:
    cap = load_capability(CAPABILITY)
    assert cap.ref == "member.lookup_share_balances@1.0.0"
    assert cap.provenance.content_hash == PINNED_HASH
    # Predicate `frame: main` (a string) and target `frame: ["nav"]` (a list) both become paths.
    outcome = cap.outcomes[0].detect
    assert isinstance(outcome, TextMatches) and outcome.text_matches.frame == ["main"]
    assert cap.steps[0].target is not None and cap.steps[0].target.frame == ["nav"]
    # `output_present: share_accounts` is a bare string inside `all`.
    assert isinstance(cap.success, AllOf)
    assert isinstance(cap.success.all[1], OutputPresent)
    # The table extract's anchor_text is a string; the anchor locator's is a list.
    extract = cap.steps[3].extract
    assert extract is not None
    table = extract["share_accounts"]
    assert isinstance(table, TableExtract) and table.table.anchor_text == "Share Accounts"
    assert table.table.parse == {"balance": "money_us", "available": "money_us"}


def test_fragile_is_derived_from_distinct_non_point_strategies() -> None:
    cap = load_capability(CAPABILITY)
    targets = {s.id: s.target for s in cap.steps if s.target is not None}
    assert targets["open_member_inquiry"].fragile is False  # a11y, text, css (+ point)
    assert targets["submit_search"].fragile is False
    data = draft(fixture_data())
    # One non-point strategy plus a point is fragile.
    data["steps"][0]["target"]["locators"] = [
        {"strategy": "text", "text": "Member Inquiry"},
        {"strategy": "point", "x": 1, "y": 2, "bound_to_fingerprint": "fp"},
    ]
    assert Capability.model_validate(data).steps[0].target.fragile is True  # type: ignore[union-attr]
    data["steps"][0]["target"]["fragile"] = False
    with pytest.raises(ValidationError, match="fragile: false"):
        Capability.model_validate(data)


def test_round_trip_is_byte_identical_and_quotes_scalars(tmp_path: Path) -> None:
    cap = load_capability(CAPABILITY)
    text = dump_model(cap)
    again = dump_model(load_model(text, Capability))
    assert text == again
    # Strings that look like numbers stay strings.
    assert "schema_version: '1.0'" in text
    assert "example: '10482'" in text
    reloaded = load_yaml(text)
    assert reloaded["schema_version"] == "1.0"
    assert reloaded["inputs"]["member_id"]["example"] == "10482"
    # And the model, and so the hash, survive.
    assert content_hash(load_model(text, Capability)) == PINNED_HASH
    written = dump_capability(cap, tmp_path / "cap.yaml")
    assert load_capability(tmp_path / "cap.yaml") == written


def test_key_order_is_preserved() -> None:
    data = draft(fixture_data())
    data["inputs"] = {
        "zeta": {"type": "string"},
        "member_id": data["inputs"]["member_id"],
        "alpha": {"type": "string", "required": False},
    }
    cap = Capability.model_validate(data)
    text = dump_model(cap)
    assert text.index("zeta:") < text.index("member_id:") < text.index("alpha:")
    table = cap.steps[3].extract["share_accounts"]  # type: ignore[index]
    assert isinstance(table, TableExtract)
    assert list(table.table.columns) == ["suffix", "description", "balance", "available"]
    # Model fields dump in declaration order: schema_version first, provenance last.
    keys = list(load_yaml(text))
    assert keys[0] == "schema_version" and keys[-1] == "provenance"


def test_hash_excludes_provenance_and_detects_edits(tmp_path: Path) -> None:
    cap = load_capability(CAPABILITY)
    reprovenanced = cap.model_copy(
        update={"provenance": cap.provenance.model_copy(update={"verified_by": []})}
    )
    assert content_hash(reprovenanced) == content_hash(cap)
    edited = CAPABILITY.read_text().replace("timeout_ms: 8000", "timeout_ms: 9000")
    path = tmp_path / "edited.yaml"
    path.write_text(edited)
    with pytest.raises(ArtifactHashMismatch, match="content changed after it was verified"):
        load_capability(path)


def test_draft_hash_is_recomputed_not_refused(tmp_path: Path) -> None:
    text = CAPABILITY.read_text().replace("status: verified", "status: draft")
    text = text.replace("timeout_ms: 8000", "timeout_ms: 9000")
    path = tmp_path / "draft.yaml"
    path.write_text(text)
    cap = load_capability(path)
    assert cap.provenance.content_hash == content_hash(cap) != PINNED_HASH


def test_verified_artifact_without_hash_is_refused(tmp_path: Path) -> None:
    text = CAPABILITY.read_text().replace(f"content_hash: {PINNED_HASH}", "content_hash: null")
    path = tmp_path / "nohash.yaml"
    path.write_text(text)
    with pytest.raises(ArtifactHashMismatch, match="must carry content_hash"):
        load_capability(path)


def test_predicate_combinators_and_not_alias() -> None:
    data = draft(fixture_data())
    data["success"] = {
        "all": [
            {"any": [{"text_present": {"text": "A"}}, {"title_matches": {"pattern": "B"}}]},
            {"not": {"text_present": {"frame": "main", "text": "SYSTEM ERROR"}}},
            {"output_present": "share_accounts"},
        ]
    }
    cap = Capability.model_validate(data)
    assert isinstance(cap.success, AllOf)
    assert isinstance(cap.success.all[0], AnyOf)
    neg = cap.success.all[1]
    assert isinstance(neg, Not) and isinstance(neg.not_, TextPresent)
    assert "not:" in dump_model(cap)
    assert sum(1 for _ in cap.predicates()) >= 6


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["steps"].append(copy.deepcopy(d["steps"][0])), "duplicate step id"),
        (lambda d: d.update(risk="write_staged"), "riskiest step makes it 'read_only'"),
        (
            lambda d: d["steps"][2].update(risk="irreversible", idempotent=False),
            "requires_approval",
        ),
        (
            lambda d: d["steps"][2].update(risk="irreversible", requires_approval=True),
            "cannot be idempotent",
        ),
        (
            lambda d: d["steps"][1]["action"].update(value="{{ inputs.ssn }}"),
            "undeclared input 'ssn'",
        ),
        (
            lambda d: d["steps"][1]["action"].update(value="{{ env.home }}"),
            "root 'env' is not allowed",
        ),
        (lambda d: d["steps"][1]["action"].update(value="{{ inputs.member_id | upper }}"), "only"),
        (lambda d: d["steps"][0].update(when="inputs.member_id = '1'"), "unexpected"),
        (lambda d: d["steps"][0].update(when="inputs.branch == 'x'"), r"undeclared \['branch'\]"),
        (lambda d: d["steps"][0].update(when="__import__('os')"), "unexpected"),
        (
            lambda d: d["steps"][3]["extract"].update(balance={"target": d["steps"][0]["target"]}),
            "undeclared output 'balance'",
        ),
        (
            lambda d: [
                d["steps"][3]["extract"]["share_accounts"]["table"][k].pop("available")
                for k in ("columns", "parse")
            ],
            "must match its item fields",
        ),
        (lambda d: d["steps"][3]["extract"].pop("member_name"), "never extracted"),
        (
            lambda d: d.update(success={"output_present": "nickname"}),
            "undeclared output 'nickname'",
        ),
        (lambda d: d["steps"][0].pop("target"), "needs a target"),
        (lambda d: d["steps"][3].update(target=d["steps"][0]["target"]), "takes no target"),
        (lambda d: d["steps"][0].update(extract={}), "only with, an extract"),
        (lambda d: d["steps"][0].update(id="Step 1"), "string_pattern_mismatch|should match"),
        (lambda d: d.update(id="lookup"), "should match"),
        (lambda d: d.update(surprise=True), "Extra inputs are not permitted"),
        (lambda d: d["outcomes"][0].update(detect={"bogus": {}}), "exactly one known key"),
        (
            lambda d: d["outcomes"][0].update(detect={"text_matches": {"pattern": "(unclosed"}}),
            "invalid regular expression",
        ),
        (
            lambda d: d["outcomes"][0].update(detect={"output_present": "member_name"}),
            "read the screen only",
        ),
        (lambda d: d["outcomes"].append(copy.deepcopy(d["outcomes"][0])), "duplicate outcome"),
    ],
)
def test_consistency_rules(mutate: Any, message: str) -> None:
    data = draft(fixture_data())
    mutate(data)
    with pytest.raises(ValidationError, match=message):
        Capability.model_validate(data)


def test_status_gates_and_unverified_sources() -> None:
    data = fixture_data()
    data["provenance"]["content_hash"] = None
    Capability.model_validate(data)  # verified with a verification run: fine
    for source in ("llm_proposed", "human_demonstrated", "probed"):
        bad = copy.deepcopy(data)
        bad["outcomes"][0]["source"] = source
        with pytest.raises(ValidationError, match="unverified outcomes"):
            Capability.model_validate(bad)
    unverified = copy.deepcopy(data)
    unverified["provenance"]["verified_by"] = []
    with pytest.raises(ValidationError, match="verification run"):
        Capability.model_validate(unverified)
    approved = copy.deepcopy(data)
    approved["status"] = "approved"
    with pytest.raises(ValidationError, match="approved_by"):
        Capability.model_validate(approved)
    approved["provenance"]["approved_by"] = {"reviewer": "m.khan", "at": "2026-10-15T13:00:00Z"}
    assert Capability.model_validate(approved).status == "approved"
    # A draft may carry LLM proposals; that is what review is for.
    proposal = draft(data)
    proposal["outcomes"][0]["source"] = "llm_proposed"
    Capability.model_validate(proposal)

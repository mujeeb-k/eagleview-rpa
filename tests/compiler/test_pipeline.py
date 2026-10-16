"""discover -> compile -> verify -> probe, offline: the scripted fake model plays Claude for the
loop and for every structured call, and verify and probe are real replays against the mock bank."""

from pathlib import Path
from typing import Any

from playwright.async_api import Browser

from eagleview.artifacts.yaml_io import load_app_profile, load_capability
from eagleview.compiler.pipeline import discover_and_compile
from eagleview.control.interventions import InterventionDesk, InterventionStore
from eagleview.discovery.agent_loop import DiscoveryConfig
from eagleview.discovery.meter import MemoryMeter
from eagleview.schema.capability import TableExtract
from eagleview.schema.predicates import AllOf, ElementPresent, FieldValue, TextPresent
from tests.discovery.fake_model import MODEL, FakeModel
from tests.discovery.test_agent_loop import GOAL, INPUTS, click_menu, finish, search
from tests.fixtures import APP_PROFILE
from tests.support.bank import Bank
from tests.support.replay import policy_for, tenant_for

NAMES = {
    "capability_id": "member.lookup_share_balances",
    "title": "Look up a member's share balances",
    "description": "Searches by member number and returns the name and every share account.",
    "steps": [
        {"index": 0, "id": "open_member_inquiry", "intent": "Open Member Inquiry.", "risk": "read"},
        {"index": 1, "id": "enter_member_id", "intent": "Type the member number.", "risk": "read"},
        {"index": 2, "id": "submit_search", "intent": "Run the search.", "risk": "write_staged"},
        {"index": 3, "id": "read_balances", "intent": "Read the outputs.", "risk": "read"},
    ],
    "outputs": [
        {"current": "member_name", "name": "member_name", "description": "Name.", "fields": []},
        {
            "current": "share_accounts",
            "name": "share_accounts",
            "description": "Shares.",
            "fields": [{"current": "sfx", "name": "suffix"}],
        },
    ],
    "inputs": [
        {
            "name": "member_id",
            "description": "Credit union member number.",
            "pattern": "^[0-9]{4,10}$",
            "example": "10482",
        }
    ],
}
PROBES = {
    "probes": [
        {"values": [{"name": "member_id", "value": "99999"}], "why": "unassigned"},
        {"values": [{"name": "member_id", "value": "10482"}], "why": "the discovery value"},
        {"values": [{"name": "member_id", "value": "abc"}], "why": "wrong shape"},
    ]
}
NOT_FOUND = {
    "business_outcome": True,
    "code": "MEMBER_NOT_FOUND",
    "message": "No member exists with that number.",
    "pattern": "(?i)no member(s)? found",
    "frame": "main",
}


async def test_a_discovery_compiles_into_a_verified_probed_capability(
    browser: Browser, lantern: Bank, tmp_path: Path
) -> None:
    desk = InterventionDesk(InterventionStore(tmp_path / "iv.sqlite"), notify=lambda _l: None)
    fake = FakeModel(desk, INPUTS, [click_menu, search, finish])
    fake.structured = {"capability_id": NAMES, "probes": PROBES, "business_outcome": NOT_FOUND}
    meter = MemoryMeter()
    result = await discover_and_compile(
        DiscoveryConfig(goal=GOAL, model=MODEL),
        profile=load_app_profile(APP_PROFILE),
        tenant=tenant_for(lantern),
        policy=policy_for(lantern),
        browser=browser,
        meter=meter,
        environ={"ANTHROPIC_API_KEY": "sk-fake", lantern.tenant.password_env: lantern.password},
        runs_root=tmp_path / "runs",
        drafts_root=tmp_path / "drafts",
        desk=desk,
        http_client=fake.client(),
    )
    assert not fake.errors, fake.errors
    assert result.status == "verified", result.review()
    assert result.path == tmp_path / "drafts" / "member.lookup_share_balances.yaml"
    assert result.path is not None
    cap = load_capability(result.path)  # the hash checks out
    assert cap.status == "verified" and cap.provenance.approved_by is None
    assert [s.id for s in cap.steps] == [
        "open_member_inquiry",
        "enter_member_id",
        "submit_search",
        "read_balances",
    ]
    assert cap.risk == "write_staged"  # raised by the naming pass, never lowered
    assert cap.inputs["member_id"].pattern == "^[0-9]{4,10}$"

    menu, fill, go, read = cap.steps
    assert isinstance(menu.expect, AllOf)
    seen, nxt = menu.expect.all
    assert isinstance(seen, TextPresent) and seen.text_present.text == "Member Search"
    assert isinstance(nxt, ElementPresent)
    assert isinstance(fill.expect, FieldValue)
    assert fill.expect.field_value.equals == "{{ inputs.member_id }}"
    assert fill.action.type == "fill" and fill.action.value == "{{ inputs.member_id }}"
    assert isinstance(go.expect, TextPresent) and "{{" not in go.expect.text_present.text

    table = read.extract["share_accounts"] if read.extract else None
    assert isinstance(table, TableExtract)
    assert table.table.anchor_text == "Share Accounts"
    assert table.table.columns["suffix"] == "Sfx"
    assert table.table.parse["balance"] == "money_us"
    items = cap.outputs["share_accounts"].items or {}
    assert items["balance"].type == "decimal" and items["description"].type == "string"
    assert cap.outputs["member_name"].sensitivity == "pii"

    # Probing found and confirmed not-found; invalid and real probes were dropped before running.
    [outcome] = cap.outcomes
    assert (outcome.code, outcome.source) == ("MEMBER_NOT_FOUND", "probed_verified")
    assert set(result.replays) == {
        "verify",
        "probe_1",
        "confirm_member_not_found",
        "final_verify",
    }
    assert len(cap.provenance.verified_by) == 2
    assert result.usd > 0

    # A second compile never replaces the first draft.
    again = await discover_and_compile(
        DiscoveryConfig(goal=GOAL, model=MODEL),
        profile=load_app_profile(APP_PROFILE),
        tenant=tenant_for(lantern),
        policy=policy_for(lantern),
        browser=browser,
        meter=meter,
        environ={"ANTHROPIC_API_KEY": "sk-fake", lantern.tenant.password_env: lantern.password},
        runs_root=tmp_path / "runs",
        drafts_root=tmp_path / "drafts",
        desk=desk,
        http_client=_fresh(fake),
    )
    assert again.path is not None and again.path.name == "member.lookup_share_balances.2.yaml"


def _fresh(fake: FakeModel) -> Any:
    fake.requests.clear()
    return fake.client()

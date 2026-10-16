"""Engine rules on a scripted fake surface: the classification order, the run deadline after an
irreversible step, and the rule that the poll interval is the only sleep in replay."""

import ast
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from eagleview.artifacts.yaml_io import load_app_profile
from eagleview.evidence.writer import EvidenceWriter
from eagleview.replay.engine import replay
from eagleview.replay.preflight import RunOptions
from eagleview.safety.redaction import Redactor
from eagleview.schema.action import Action
from eagleview.schema.app_profile import AppProfile, GlobalState
from eagleview.schema.capability import Capability
from eagleview.schema.result import RunResult
from eagleview.schema.tenant import Credentials, TenantProfile
from tests.fixtures import APP_PROFILE, REPO
from tests.replay.fake_surface import FakeSurface, Script, opener_for

TENANT = TenantProfile(
    slug="lantern-bay",
    name="Lantern Bay Federal Credit Union",
    app_profile="ironbridge-teller",
    base_url="http://127.0.0.1:8601",
    credentials=Credentials(username="teller01", password="secret://unit_password"),
)
ENV = {"UNIT_PASSWORD": "unit-test-password"}


def capability(steps: list[dict[str, Any]], **extra: Any) -> Capability:
    risks = {s.get("risk", "read") for s in steps}
    risk = "irreversible" if "irreversible" in risks else "read_only"
    return Capability.model_validate(
        {
            "schema_version": "1.0",
            "id": "test.unit",
            "version": "0.1.0",
            "title": "Unit",
            "description": "Unit test capability.",
            "app": {"profile": "ironbridge-teller", "versions": ">=7.2,<8.0"},
            "risk": risk,
            "steps": steps,
            "success": {"text_present": {"text": "Done"}},
            **extra,
        }
    )


def click(step_id: str, expect: str = "Done", **extra: Any) -> dict[str, Any]:
    return {
        "id": step_id,
        "action": {"type": "click"},
        "target": {"locators": [{"strategy": "text", "text": step_id}]},
        "expect": {"text_present": {"text": expect}},
        **extra,
    }


async def run(
    tmp_path: Path,
    cap: Capability,
    scripts: dict[str, Script],
    options: RunOptions | None = None,
    profile: AppProfile | None = None,
) -> tuple[RunResult, FakeSurface]:
    made: list[FakeSurface] = []
    writer = EvidenceWriter(tmp_path / "runs", "r_unit", Redactor())
    result = await replay(
        cap,
        {},
        profile=profile or load_app_profile(APP_PROFILE),
        tenant=TENANT,
        open_surface=opener_for(scripts, made),
        writer=writer,
        environ=ENV,
        options=options,
    )
    return result, made[0]


def shows(*texts: str) -> Callable[[FakeSurface, Action], None]:
    return lambda surface, _action: surface.show(*texts)


# --- classification order -------------------------------------------------------------------------

OUTCOME = {
    "outcomes": [
        {
            "code": "MEMBER_NOT_FOUND",
            "detect": {"text_present": {"text": "NO MEMBER FOUND"}},
            "message": "No such member.",
        }
    ]
}


async def test_profile_states_beat_declared_outcomes_beat_expect(tmp_path: Path) -> None:
    cap = capability([click("go")], **OUTCOME)
    result, _ = await run(tmp_path, cap, {"go": shows("ACCESS DENIED", "NO MEMBER FOUND", "Done")})
    assert result.status == "outcome"
    assert result.outcome is not None and result.outcome.code == "NOT_PERMITTED"


async def test_declared_outcomes_beat_interstitials_and_expect(tmp_path: Path) -> None:
    cap = capability([click("go")], **OUTCOME)
    result, _ = await run(tmp_path, cap, {"go": shows("System Notice", "NO MEMBER FOUND", "Done")})
    assert result.outcome is not None and result.outcome.code == "MEMBER_NOT_FOUND"
    assert result.recoveries == []


async def test_interstitials_are_cleared_before_expect_is_accepted(tmp_path: Path) -> None:
    def ok(surface: FakeSurface, _action: Action) -> None:
        surface.hide("System Notice")

    cap = capability([click("go")])
    result, surface = await run(tmp_path, cap, {"go": shows("System Notice", "Done"), "OK": ok})
    assert result.status == "success", result.failure
    assert [r.handler for r in result.recoveries] == ["system_notice.dismiss"]
    assert ("go", "click") in surface.acts


async def test_a_click_sequence_handler_clicks_each_target_in_order(tmp_path: Path) -> None:
    base = load_app_profile(APP_PROFILE)
    drawer = GlobalState.model_validate(
        {
            "id": "drawer_alert",
            "detect": {"text_present": {"text": "Cash Drawer Alert"}},
            "recover": {
                "type": "click_sequence",
                "clicks": [
                    {"locators": [{"strategy": "text", "text": "Supervisor notified"}]},
                    {"locators": [{"strategy": "text", "text": "Continue"}]},
                ],
            },
            "max_per_run": 1,
            "source": "human_demonstrated",
        }
    )
    profile = base.model_copy(update={"global_states": [*base.global_states, drawer]})

    def cleared(surface: FakeSurface, _action: Action) -> None:
        surface.hide("Cash Drawer Alert")

    result, surface = await run(
        tmp_path,
        capability([click("go")]),
        {"go": shows("Cash Drawer Alert", "Done"), "Continue": cleared},
        profile=profile,
    )
    assert result.status == "success", result.failure
    assert [r.handler for r in result.recoveries] == ["drawer_alert.click_sequence"]
    clicks = [a for a in surface.acts if a[0] == "go"]
    assert len(clicks) == 3  # the step's own click, then the two handler clicks


# --- after an irreversible step -------------------------------------------------------------------


async def test_run_deadline_after_a_commit_is_outcome_uncertain(tmp_path: Path) -> None:
    post = click(
        "post",
        expect="Posted",
        risk="irreversible",
        idempotent=False,
        requires_approval=True,
        timeout_ms=60_000,
    )
    cap = capability([click("open", expect="Review"), post])
    result, surface = await run(
        tmp_path,
        cap,
        {"open": shows("Review")},  # Post is sent, and the confirmation never shows
        RunOptions(approve_gates=True, deadline_s=1.5),
    )
    assert result.status == "failed"
    assert result.failure is not None
    assert (result.failure.category, result.failure.step_id) == ("OUTCOME_UNCERTAIN", "post")
    assert "run deadline" in (result.failure.message or "")
    assert not result.failure.retryable
    assert [a for a in surface.acts if a[0] == "post"] == [("post", "click")]


async def test_session_expiry_after_a_commit_never_signs_on_again(tmp_path: Path) -> None:
    post = click("post", risk="irreversible", idempotent=False, requires_approval=True)
    cap = capability([post])
    result, surface = await run(
        tmp_path, cap, {"post": shows("SESSION EXPIRED")}, RunOptions(approve_gates=True)
    )
    assert result.failure is not None and result.failure.category == "OUTCOME_UNCERTAIN"
    assert "SESSION_LOST" in (result.failure.message or "")
    assert [a for a in surface.acts if a[0] == "login.sign_on"] == [("login.sign_on", "click")]


# --- invariant 9: the poll interval is the only sleep ---------------------------------------------


def test_the_poll_interval_is_the_only_sleep_in_replay() -> None:
    sleeps: list[str] = []
    for path in sorted((REPO / "src" / "eagleview" / "replay").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        scopes = {
            id(n): n.name
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Attribute) and node.attr in (
                    "sleep",
                    "wait_for_timeout",
                ):
                    sleeps.append(f"{path.name}:{scopes[id(fn)]}")
    assert sleeps == ["settle.py:pause"]


@pytest.mark.parametrize("source", ["time.sleep", "asyncio.sleep"])
def test_the_sleep_check_sees_sleeps(source: str) -> None:
    tree = ast.parse(f"async def f():\n    {source}(1)\n")
    assert any(isinstance(n, ast.Attribute) and n.attr == "sleep" for n in ast.walk(tree))

"""One integration test per replay path, against the real mock bank in a real browser. No LLM.

Each test arms at most one fault, replays the hand-written lookup fixture on Lantern Bay, and checks
the RunResult contract: status, payload, recoveries and the locator report."""

from decimal import Decimal
from functools import cache

from mock_bank.config import all_tenants
from mock_bank.seed import Member, generate
from tests.support.bank import Bank
from tests.support.replay import Replay

DEMO = "10482"
LOOKUP_STEPS = ["open_member_inquiry", "enter_member_id", "submit_search"]


@cache
def _seeded(slug: str) -> dict[str, Member]:
    return generate(next(t for t in all_tenants() if t.slug == slug))


def _members(slug: str, bank: Bank) -> dict[str, Member]:
    return _seeded(slug)


def expected_shares(bank: Bank, member: str) -> list[dict[str, object]]:
    m = _members(bank.tenant.slug, bank)[member]
    return [
        {
            "suffix": s.suffix,
            "description": s.description,
            "balance": s.balance,
            "available": s.available,
        }
        for s in m.shares
    ]


def handlers(run_result: object) -> list[tuple[str, str | None]]:
    return [(r.handler, r.at_step) for r in run_result.recoveries]  # type: ignore[attr-defined]


# --- success --------------------------------------------------------------------------------------


async def test_success_returns_typed_outputs_and_a_locator_report(
    replay: Replay, lantern: Bank
) -> None:
    run = await replay(lantern, {"member_id": DEMO})
    r = run.result
    assert r.status == "success", r.failure
    assert r.outputs is not None
    assert r.outputs["member_name"] == _members("lantern-bay", lantern)[DEMO].display_name
    assert r.outputs["share_accounts"] == expected_shares(lantern, DEMO)
    balances = [row["balance"] for row in r.outputs["share_accounts"]]  # type: ignore[union-attr]
    assert all(isinstance(b, Decimal) for b in balances)
    assert r.recoveries == []
    # Every acted step reports which locator won, its rank, and whether the next one agreed.
    report = {(x.step_id, x.strategy, x.rank, x.agreement) for x in r.locator_report}
    assert report == {
        ("open_member_inquiry", "a11y", 0, True),
        ("enter_member_id", "anchor", 0, True),
        ("submit_search", "anchor", 0, True),
    }
    assert r.effective_hash is not None and r.effective_hash.startswith("sha256:")
    assert set(LOOKUP_STEPS) <= set(r.timing_ms) and "total" in r.timing_ms
    # The JSON contract: money is a string, never a float.
    wire = r.model_dump(mode="json")
    assert wire["outputs"]["share_accounts"][0]["balance"] == str(balances[0])
    assert (run.run_dir / "summary.json").is_file()
    assert sorted(p.name for p in (run.run_dir / "steps").iterdir())[:2] == [
        "0001_after.png",
        "0001_before.png",
    ]


async def test_padded_input_is_trimmed_and_succeeds(replay: Replay, lantern: Bank) -> None:
    run = await replay(lantern, {"member_id": f"  {DEMO} "})
    assert run.result.status == "success"


async def test_empty_share_list_is_an_answer(replay: Replay, lantern: Bank) -> None:
    loans_only = lantern.tenant.edge_cases.loans_only_member
    run = await replay(lantern, {"member_id": loans_only})
    assert run.result.status == "success", run.result.failure
    assert run.result.outputs is not None and run.result.outputs["share_accounts"] == []


async def test_overdrawn_balance_parses_negative(replay: Replay, lantern: Bank) -> None:
    edge = lantern.tenant.edge_cases
    run = await replay(lantern, {"member_id": edge.overdrawn_member})
    assert run.result.status == "success", run.result.failure
    rows = run.result.outputs["share_accounts"]  # type: ignore[index]
    assert Decimal(edge.overdrawn_balance) in [row["balance"] for row in rows]  # type: ignore[index, union-attr]


# --- outcomes -------------------------------------------------------------------------------------


async def test_member_not_found_is_an_outcome(replay: Replay, lantern: Bank) -> None:
    run = await replay(lantern, {"member_id": "99999"})
    r = run.result
    assert r.status == "outcome"
    assert r.outcome is not None
    assert (r.outcome.code, r.outcome.at_step) == ("MEMBER_NOT_FOUND", "submit_search")
    assert r.failure is None and r.outputs is None


# --- rejection ------------------------------------------------------------------------------------


async def test_bad_input_is_rejected_before_the_ui_is_touched(
    replay: Replay, lantern: Bank
) -> None:
    for inputs, code in [
        ({"member_id": "10 482"}, "pattern"),
        ({"member_id": "abc"}, "pattern"),
        ({}, "missing"),
        ({"member_id": DEMO, "branch": "x"}, "unknown"),
        ({"member_id": 10482}, "type"),
    ]:
        run = await replay(lantern, inputs)
        r = run.result
        assert r.status == "rejected", inputs
        assert r.rejection is not None and r.rejection.code == "INPUT_INVALID"
        assert code in [e.code for e in r.rejection.errors]
        assert not run.of("surface.act"), "a rejected run never acts"
        assert not (run.run_dir / "steps").exists()


# --- recoveries -----------------------------------------------------------------------------------


async def test_system_notice_is_dismissed(replay: Replay, lantern: Bank) -> None:
    lantern.set_faults(system_notice_on="/member")
    run = await replay(lantern, {"member_id": DEMO})
    assert run.result.status == "success", run.result.failure
    assert handlers(run.result) == [("system_notice.dismiss", "submit_search")]


async def test_session_expiry_before_any_commit_signs_on_again_and_restarts(
    replay: Replay, lantern: Bank
) -> None:
    # Sign-on itself loads four authenticated pages (frames, banner, nav, home), so the fifth
    # request, GET /inq from the first step, is the one that finds the session expired.
    lantern.set_faults(session_expire_after_requests="4")
    run = await replay(lantern, {"member_id": DEMO})
    assert run.result.status == "success", run.result.failure
    assert handlers(run.result) == [("session_expired.relogin_and_restart", "open_member_inquiry")]
    assert len(run.of("session.signed_on")) == 2
    assert run.result.outputs is not None
    assert run.result.outputs["share_accounts"] == expected_shares(lantern, DEMO)


async def test_slow_load_settles_without_a_fixed_wait(replay: Replay, lantern: Bank) -> None:
    lantern.set_faults(slow_ms="/member:2500")
    run = await replay(lantern, {"member_id": DEMO})
    assert run.result.status == "success", run.result.failure
    assert run.result.timing_ms["submit_search"] >= 2500
    assert run.result.recoveries == []


async def test_error500_once_retries_the_idempotent_step(replay: Replay, lantern: Bank) -> None:
    # The route matches GET /inq, the page the first step opens; retrying the click reloads it.
    lantern.set_faults(error500_on="/inq:once")
    run = await replay(lantern, {"member_id": DEMO})
    assert run.result.status == "success", run.result.failure
    assert handlers(run.result) == [("app_error.retry_step_if_idempotent", "open_member_inquiry")]


async def test_error500_always_fails_app_error(replay: Replay, lantern: Bank) -> None:
    lantern.set_faults(error500_on="/inq")
    run = await replay(lantern, {"member_id": DEMO})
    r = run.result
    assert r.status == "failed"
    assert r.failure is not None
    assert (r.failure.category, r.failure.step_id) == ("APP_ERROR", "open_member_inquiry")
    assert not r.failure.retryable
    assert handlers(r) == [("app_error.retry_step_if_idempotent", "open_member_inquiry")]


# --- hard states ----------------------------------------------------------------------------------


async def test_unknown_modal_is_unknown_state_with_evidence(replay: Replay, lantern: Bank) -> None:
    lantern.set_faults(unknown_modal_on="/member")
    run = await replay(lantern, {"member_id": DEMO})
    r = run.result
    assert r.status == "failed"
    assert r.failure is not None
    assert (r.failure.category, r.failure.step_id) == ("UNKNOWN_STATE", "submit_search")
    assert r.failure.expected is not None and "Share Accounts" in r.failure.expected
    # The page moved on to /member, so this is an unrecognized screen, not "nothing changed".
    assert r.failure.observed is not None and "main=/member" in r.failure.observed
    assert "nothing changed" not in (r.failure.message or "")
    ev = r.failure.evidence
    assert ev.screenshot is not None and (run.run_dir / ev.screenshot).is_file()
    assert ev.dom_snapshot is not None and (run.run_dir / ev.dom_snapshot).is_file()
    assert ev.events == "run.jsonl"


async def test_maintenance_fails_app_unavailable_and_retryable(
    replay: Replay, lantern: Bank
) -> None:
    lantern.set_faults(maintenance="on")
    run = await replay(lantern, {"member_id": DEMO})
    r = run.result
    assert r.status == "failed"
    assert r.failure is not None
    assert r.failure.category == "APP_UNAVAILABLE" and r.failure.retryable
    assert r.failure.step_id is None  # stopped before the first step
    assert not run.of("step.start")


async def test_escalate_parks_the_failure_for_a_human(replay: Replay, lantern: Bank) -> None:
    from eagleview.replay.preflight import RunOptions

    lantern.set_faults(unknown_modal_on="/member")
    run = await replay(lantern, {"member_id": DEMO}, options=RunOptions(escalate=True))
    r = run.result
    assert r.status == "needs_human"
    assert r.failure is not None and r.failure.category == "UNKNOWN_STATE"
    assert [i.kind for i in r.interventions] == ["takeover"]


# --- redaction ------------------------------------------------------------------------------------


async def test_nothing_the_run_read_or_typed_is_written_readable(
    replay: Replay, lantern: Bank
) -> None:
    run = await replay(lantern, {"member_id": DEMO})
    assert run.result.status == "success"
    written = run.files_text()
    member = _members("lantern-bay", lantern)[DEMO]
    assert DEMO not in written
    assert member.display_name not in written
    assert lantern.password not in written
    sign_on_fills = [e for e in run.of("surface.act") if e["actor"] == "harness"]
    assert sign_on_fills and all(e["text_length"] is None for e in sign_on_fills)
    for share in member.shares:
        assert f"{share.balance:,}" not in written  # default policy masks amounts
    # The caller still gets the real values.
    assert run.result.outputs is not None
    assert run.result.outputs["member_name"] == member.display_name


# --- known limit until P8 -------------------------------------------------------------------------


async def test_copperhill_without_its_overlay_stops_at_select_branch(
    replay: Replay, copperhill: Bank
) -> None:
    # Copperhill asks for a branch after sign-on. Its handler lives in the tenant overlay, which P8
    # resolves; until then the run reports the unrecognized screen rather than guessing.
    run = await replay(copperhill, {"member_id": copperhill.demo})
    r = run.result
    assert r.status == "failed"
    assert r.failure is not None and r.failure.category == "UNKNOWN_STATE"
    assert "sign-on did not reach its success state" in (r.failure.message or "")
    assert r.failure.observed is not None and "/branch" in r.failure.observed


async def test_failure_evidence_hides_personal_data_the_run_never_read(
    replay: Replay, lantern: Bank
) -> None:
    # The run fails on Member Detail under a translucent modal, before it extracts anything. The
    # app profile's sensitive labels still keep the address, phone and notes off disk.
    lantern.set_faults(unknown_modal_on="/member")
    run = await replay(lantern, {"member_id": DEMO})
    assert run.result.failure is not None
    assert run.result.failure.evidence.dom_snapshot is not None
    m = _members("lantern-bay", lantern)[DEMO]
    written = run.files_text()
    assert "Member Detail" in written  # the snapshot is there, and readable
    for value in [m.display_name, m.phone, m.email, m.street, f"{m.city}, {m.state} {m.zip}"]:
        assert value not in written, value
    if m.notes:
        assert m.notes not in written

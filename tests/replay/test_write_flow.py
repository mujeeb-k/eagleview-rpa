"""The irreversible path, through Post, against the real mock bank. The bank has no double-post
guard of its own, so these tests count the member's share accounts to prove what replay did.

Once Post has been sent, any result other than success is OUTCOME_UNCERTAIN, with no retry and no
sign-on again: a human checks the app before anyone tries again."""

from pathlib import Path

import pytest

from eagleview.replay.preflight import RunOptions
from tests.fixtures import OPEN_SHARE
from tests.support.bank import Bank
from tests.support.replay import Replay, approved_copy, share_rows

DEMO = "10482"
INPUTS = {"member_id": DEMO, "share_type": "Money Market"}
APPROVED = RunOptions(approve_gates=True)

# Authenticated page requests up to and including POST /share/post: sign-on 4 (frames, banner,
# nav, home), then GET /inq, POST /inq, GET /member, GET /share/open, POST /share/open,
# GET /share/review and POST /share/post. The next one, GET /share/posted, finds the session gone.
REQUESTS_THROUGH_POST = 11


async def test_approved_post_opens_exactly_one_share(replay: Replay, lantern: Bank) -> None:
    before = share_rows(lantern, DEMO)
    run = await replay(lantern, INPUTS, capability=OPEN_SHARE, options=APPROVED)
    r = run.result
    assert r.status == "success", r.failure
    assert r.outputs is not None and str(r.outputs["transaction_number"]).isdigit()
    assert [(i.kind, i.id) for i in r.interventions] == [("approval", "approval.post_share")]
    assert share_rows(lantern, DEMO) == before + 1


async def test_without_approval_post_is_blocked_before_acting(
    replay: Replay, lantern: Bank
) -> None:
    before = share_rows(lantern, DEMO)
    run = await replay(lantern, INPUTS, capability=OPEN_SHARE)
    r = run.result
    assert r.status == "failed"
    assert r.failure is not None
    assert (r.failure.category, r.failure.step_id) == ("POLICY_BLOCKED", "post_share")
    assert "--approve-gates" in (r.failure.message or "")
    assert not [e for e in run.of("surface.act") if e.get("step_id") == "post_share"]
    assert share_rows(lantern, DEMO) == before


def test_unattended_cannot_be_combined_with_approve_gates() -> None:
    with pytest.raises(ValueError, match="never approves its own irreversible steps"):
        RunOptions(unattended=True, approve_gates=True)


async def test_unattended_run_blocks_at_the_irreversible_step(
    replay: Replay, lantern: Bank, tmp_path: Path
) -> None:
    approved = approved_copy(OPEN_SHARE, tmp_path)
    before = share_rows(lantern, DEMO)
    run = await replay(lantern, INPUTS, capability=approved, options=RunOptions(unattended=True))
    r = run.result
    assert r.status == "failed"
    assert r.failure is not None
    assert (r.failure.category, r.failure.step_id) == ("POLICY_BLOCKED", "post_share")
    assert "unattended" in (r.failure.message or "")
    assert r.interventions == []
    assert share_rows(lantern, DEMO) == before


async def test_access_denied_is_an_outcome_not_a_failure(replay: Replay, lantern: Bank) -> None:
    lantern.set_faults(role="inquiry_only")
    run = await replay(lantern, INPUTS, capability=OPEN_SHARE, options=APPROVED)
    r = run.result
    assert r.status == "outcome"
    assert r.failure is None
    assert r.outcome is not None
    assert (r.outcome.code, r.outcome.at_step) == ("NOT_PERMITTED", "open_share_form")


async def test_a_form_the_app_refuses_is_a_validation_outcome_quoting_the_app(
    replay: Replay, lantern: Bank
) -> None:
    """The demo member already has a Holiday Club share, so the app refuses a second one with
    IBT-2210 and keeps the form on screen. That is the app's answer, not a timeout."""
    before = share_rows(lantern, DEMO)
    inputs = {"member_id": DEMO, "share_type": "Holiday Club"}
    run = await replay(lantern, inputs, capability=OPEN_SHARE, options=APPROVED)
    r = run.result
    assert r.status == "outcome", r.failure
    assert r.failure is None
    assert r.outcome is not None
    assert (r.outcome.code, r.outcome.at_step) == ("VALIDATION_ERROR", "validate_share")
    assert r.outcome.message == (
        "The teller app refused the form: IBT-2210 MEMBER ALREADY HAS A HOLIDAY CLUB SHARE"
    )
    assert not [e for e in run.of("surface.act") if e.get("step_id") == "post_share"]
    assert share_rows(lantern, DEMO) == before


@pytest.mark.parametrize(
    ("faults", "cause"),
    [
        ({"session_expire_after_requests": str(REQUESTS_THROUGH_POST)}, "SESSION_LOST"),
        ({"error500_on": "/share/posted"}, "APP_ERROR"),
        ({"unknown_modal_on": "/share/posted"}, "UNKNOWN_STATE"),
    ],
    ids=["session_expired", "app_error", "unknown_state"],
)
async def test_anything_but_success_after_post_is_outcome_uncertain(
    replay: Replay, lantern: Bank, faults: dict[str, str], cause: str
) -> None:
    before = share_rows(lantern, DEMO)
    lantern.set_faults(**faults)
    run = await replay(lantern, INPUTS, capability=OPEN_SHARE, options=APPROVED)
    r = run.result
    assert r.status == "failed"
    assert r.failure is not None
    assert (r.failure.category, r.failure.step_id) == ("OUTCOME_UNCERTAIN", "post_share")
    assert not r.failure.retryable
    assert cause in (r.failure.message or "")
    # Nothing was retried and nobody signed on again after the commit.
    assert r.recoveries == []
    assert len(run.of("session.signed_on")) == 1
    posts = [e for e in run.of("surface.act") if e.get("step_id") == "post_share"]
    assert len(posts) == 1
    assert share_rows(lantern, DEMO) == before + 1

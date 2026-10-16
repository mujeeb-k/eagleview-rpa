"""The guards around a run: the stale-expect rule, the run deadline, the status gate for
unattended runs, and edited artifacts."""

from pathlib import Path

import pytest

from eagleview.artifacts.loader import load_for_run
from eagleview.artifacts.yaml_io import ArtifactHashMismatch
from eagleview.replay.preflight import RunOptions
from tests.fixtures import CAPABILITY, STALE_EXPECT
from tests.support.bank import Bank
from tests.support.replay import Replay, approved_copy

DEMO = "10482"

# --- stale expect ---------------------------------------------------------------------------------


async def test_true_before_expect_passes_once_the_page_reloads(
    replay: Replay, lantern: Bank
) -> None:
    run = await replay(lantern, {}, capability=STALE_EXPECT)
    assert run.result.status == "success", run.result.failure
    done = [e["step_id"] for e in run.of("step.done")]
    assert done == ["open_member_inquiry", "reopen_member_inquiry"]


async def test_true_before_expect_does_not_pass_a_click_that_changed_nothing(
    replay: Replay, lantern: Bank
) -> None:
    # "Member Search" already shows, and clicking a plain caption changes nothing. Without the
    # guard the step would pass on what was showing before it acted.
    run = await replay(lantern, {"noop_click": True}, capability=STALE_EXPECT)
    r = run.result
    assert r.status == "failed"
    assert r.failure is not None
    assert (r.failure.category, r.failure.step_id) == ("STATE_TIMEOUT", "click_caption")
    assert "nothing changed" in (r.failure.message or "")


# --- run deadline ---------------------------------------------------------------------------------


async def test_run_deadline_fails_cleanly_at_the_step_reached(
    replay: Replay, lantern: Bank
) -> None:
    lantern.set_faults(slow_ms="/member:6000")
    run = await replay(lantern, {"member_id": DEMO}, options=RunOptions(deadline_s=3))
    r = run.result
    assert r.status == "failed"
    assert r.failure is not None
    assert (r.failure.category, r.failure.step_id) == ("STATE_TIMEOUT", "submit_search")
    assert "run deadline of 3s" in (r.failure.message or "")
    assert r.timing_ms["total"] < 5000


def test_deadline_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        RunOptions(deadline_s=0)


# --- status gate ----------------------------------------------------------------------------------


async def test_unattended_needs_an_approved_capability(replay: Replay, lantern: Bank) -> None:
    run = await replay(lantern, {"member_id": DEMO}, options=RunOptions(unattended=True))
    r = run.result
    assert r.status == "rejected"
    assert r.rejection is not None and r.rejection.code == "CAPABILITY_NOT_APPROVED"
    assert "verified" in (r.rejection.message or "")
    assert not run.of("surface.act")


async def test_unattended_runs_an_approved_capability(
    replay: Replay, lantern: Bank, tmp_path: Path
) -> None:
    approved = approved_copy(CAPABILITY, tmp_path)
    run = await replay(
        lantern, {"member_id": DEMO}, capability=approved, options=RunOptions(unattended=True)
    )
    assert run.result.status == "success", run.result.failure


# --- edited artifacts -----------------------------------------------------------------------------


def _edited(tmp_path: Path) -> Path:
    path = tmp_path / "edited.yaml"
    text = CAPABILITY.read_text(encoding="utf-8")
    path.write_text(text.replace("Look up a member's", "Look up the member's"), encoding="utf-8")
    return path


def test_edited_verified_artifact_is_refused_with_guidance(tmp_path: Path) -> None:
    path = _edited(tmp_path)
    with pytest.raises(ArtifactHashMismatch) as caught:
        load_for_run(path)
    message = str(caught.value)
    assert str(path) in message
    assert "status: draft" in message and "--allow-modified" in message


async def test_allow_modified_runs_as_a_draft_and_says_so(
    replay: Replay, lantern: Bank, tmp_path: Path
) -> None:
    path = _edited(tmp_path)
    assert load_for_run(path, allow_modified=True).capability.status == "draft"
    run = await replay(lantern, {"member_id": DEMO}, capability=path, allow_modified=True)
    assert run.result.status == "success", run.result.failure
    assert run.result.modified is True
    unattended = await replay(
        lantern,
        {"member_id": DEMO},
        capability=path,
        allow_modified=True,
        options=RunOptions(unattended=True),
    )
    assert unattended.result.status == "rejected"
    assert unattended.result.rejection is not None
    assert unattended.result.rejection.code == "CAPABILITY_NOT_APPROVED"
    assert unattended.result.modified is True


async def test_unmodified_runs_record_no_modified_flag(replay: Replay, lantern: Bank) -> None:
    run = await replay(lantern, {"member_id": "99999"})
    assert run.result.modified is None
    assert "modified" not in run.result.model_dump(exclude_none=True)

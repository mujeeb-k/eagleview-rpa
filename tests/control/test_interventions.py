"""The interventions store and desk, without a browser."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from eagleview.control.interventions import InterventionDesk, InterventionStore, operator_actor
from eagleview.schema.intervention import Intervention


def make(iv_id: str = "iv_one", run_id: str = "r_store") -> Intervention:
    return Intervention(
        id=iv_id,
        kind="approval",
        run_id=run_id,
        capability="member.open_share@0.1.0",
        step_id="post_share",
        reason="APPROVAL_REQUIRED",
        state_summary="Review - Open Share",
        allowed_actions=["approve", "deny"],
        created_at=datetime(2026, 10, 16, tzinfo=UTC),
    )


def test_store_round_trip_lists_and_resolves(tmp_path: Path) -> None:
    store = InterventionStore(tmp_path / "iv.sqlite")
    store.add(make())
    store.add(make("iv_two", "r_other"))
    assert store.get("iv_one") == make()
    assert [iv.id for iv in store.list(open_only=True)] == ["iv_one", "iv_two"]
    assert [iv.id for iv in store.list(run_id="r_other")] == ["iv_two"]
    done = store.resolve("iv_one", "approved", "operator:ana", "checked the form")
    assert (done.resolution, done.resolved_by, done.note) == (
        "approved",
        "operator:ana",
        "checked the form",
    )
    assert done.resolved_at is not None
    assert [iv.id for iv in store.list(open_only=True)] == ["iv_two"]
    with pytest.raises(ValueError, match="already approved"):
        store.resolve("iv_one", "denied", "operator:bo")
    store.close()
    # It is a file: a fresh store sees the same rows.
    again = InterventionStore(tmp_path / "iv.sqlite")
    assert again.get("iv_one") == done
    again.close()


async def test_desk_wait_returns_on_resolution_or_times_out(tmp_path: Path) -> None:
    lines: list[str] = []
    desk = InterventionDesk(InterventionStore(tmp_path / "iv.sqlite"), notify=lines.append)
    iv = desk.open(
        kind="approval",
        run_id="r_desk",
        capability="member.open_share@0.1.0",
        step_id="post_share",
        reason="APPROVAL_REQUIRED",
        state_summary="Review",
        screenshot=None,
        allowed=["approve", "deny"],
    )
    assert lines and "approval needed for r_desk" in lines[0]
    waiter = asyncio.create_task(desk.wait(iv.id, timeout_s=5))
    await asyncio.sleep(0)
    desk.resolve(iv.id, "denied", "operator:ana")
    assert (await waiter).resolution == "denied"
    late = desk.open(
        kind="approval",
        run_id="r_desk",
        capability="member.open_share@0.1.0",
        step_id="post_share",
        reason="APPROVAL_REQUIRED",
        state_summary="Review",
        screenshot=None,
        allowed=["approve", "deny"],
    )
    assert (await desk.wait(late.id, timeout_s=0.05)).resolution == "timed_out"


def test_operator_names_are_checked_at_the_boundary() -> None:
    assert operator_actor("ana.m") == "operator:ana.m"
    for bad in ["", "Ana", "ana bo", "x" * 65, "automation"]:
        if bad == "automation":
            assert operator_actor(bad) == "operator:automation"  # always namespaced
            continue
        with pytest.raises(ValueError):
            operator_actor(bad)

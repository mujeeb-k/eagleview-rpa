"""The lease and its state machine: every edge PLAN §5 allows works, every other one is refused,
and the epoch fences out anyone acting with an old one."""

import asyncio
import itertools

import pytest

from eagleview.control.session_controller import (
    TERMINAL,
    TRANSITIONS,
    ControlState,
    InvalidTransition,
    SessionController,
)
from eagleview.surfaces.base import NotHolder

S = ControlState
ALL = list(S)


@pytest.mark.parametrize(("start", "to"), list(itertools.product(ALL, ALL)))
def test_every_transition_is_allowed_or_refused_as_the_table_says(
    start: ControlState, to: ControlState
) -> None:
    ctl = SessionController("r_sm")
    ctl.state = start
    if to in TRANSITIONS[start]:
        ctl._move(to, holder=None, by="test")  # pyright: ignore[reportPrivateUsage]
        assert ctl.state is to
    else:
        with pytest.raises(InvalidTransition):
            ctl._move(to, holder=None, by="test")  # pyright: ignore[reportPrivateUsage]
        assert ctl.state is start


def test_terminal_states_have_no_way_out() -> None:
    assert all(not TRANSITIONS[t] for t in TERMINAL)


def test_escalate_take_resume_validate_round_trip_bumps_the_epoch() -> None:
    ctl = SessionController("r_sm", actor="automation")
    epochs = [ctl.epoch]
    ctl.escalate()
    assert (ctl.state, ctl.holder) == (S.AWAITING_HUMAN, None)
    ctl.take_control("operator:ana")
    assert (ctl.state, ctl.holder) == (S.HUMAN, "operator:ana")
    epochs.append(ctl.epoch)
    ctl.resume("operator:ana")
    ctl.start_validation()
    ctl.resume_automation()
    assert (ctl.state, ctl.holder) == (S.AUTOMATION, "automation")
    epochs.append(ctl.epoch)
    assert epochs == sorted(set(epochs))  # strictly increasing


def test_a_stale_epoch_or_a_foreign_actor_is_refused() -> None:
    ctl = SessionController("r_sm", actor="automation")
    old = ctl.epoch
    ctl.assert_holder("automation", old)
    ctl.force_take("operator:ana")
    with pytest.raises(NotHolder):
        ctl.assert_holder("automation", old)  # preempted: its next act is refused
    with pytest.raises(NotHolder):
        ctl.assert_holder("operator:ana", old)  # right actor, old epoch
    ctl.assert_holder("operator:ana", ctl.epoch)


def test_nobody_may_act_while_the_session_changes_hands() -> None:
    ctl = SessionController("r_sm", actor="automation")
    ctl.escalate()
    with pytest.raises(NotHolder):
        ctl.assert_holder("automation", ctl.epoch)


def test_only_the_holder_resumes_and_only_from_human() -> None:
    ctl = SessionController("r_sm", actor="automation")
    with pytest.raises(InvalidTransition):
        ctl.resume("operator:ana")
    ctl.force_take("operator:ana")
    with pytest.raises(InvalidTransition):
        ctl.resume("operator:bo")
    with pytest.raises(InvalidTransition):
        ctl.take_control("operator:bo")  # take is for AWAITING_HUMAN; HUMAN is held
    ctl.resume("operator:ana")


def test_pause_is_cooperative_until_automation_yields() -> None:
    ctl = SessionController("r_sm", actor="automation")
    epoch = ctl.epoch
    ctl.request_pause("operator:ana")
    assert ctl.state is S.PAUSE_REQUESTED
    ctl.assert_holder("automation", epoch)  # automation still holds until the boundary
    ctl.yield_to_human()
    assert ctl.state is S.AWAITING_HUMAN


def test_hand_to_moves_between_harness_and_automation_only() -> None:
    ctl = SessionController("r_sm")
    ctl.hand_to("automation")
    assert ctl.holder == "automation"
    with pytest.raises(InvalidTransition):
        ctl.hand_to("operator:ana")
    ctl.escalate()
    with pytest.raises(InvalidTransition):
        ctl.hand_to("harness")


async def test_wait_for_wakes_on_the_transition() -> None:
    ctl = SessionController("r_sm", actor="automation")
    waiter = asyncio.create_task(ctl.wait_for({S.RESUME_REQUESTED, S.ABORTED}))
    await asyncio.sleep(0)
    assert not waiter.done()
    ctl.escalate()
    ctl.take_control("operator:ana")
    await asyncio.sleep(0)
    assert not waiter.done()
    ctl.resume("operator:ana")
    assert await asyncio.wait_for(waiter, 1) is S.RESUME_REQUESTED

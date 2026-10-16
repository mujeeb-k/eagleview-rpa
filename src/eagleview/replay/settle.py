"""Settling: looking at the screen until it says what happened.

Every wait in replay is a loop of predicate checks with a deadline. `pause()` is the interval
between two looks and the only sleep anywhere in `eagleview.replay`; a test enforces that. There is
no fixed delay after an action: the loop moves on the moment a predicate holds.

Each look classifies the screen in a fixed priority order, first match wins:
1. hard global states from the app profile (session expired, system error, access denied,
   maintenance), in profile order;
2. the capability's declared business outcomes;
3. interstitials a handler can clear (a System Notice to dismiss), in profile order;
4. the step's own `expect`.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from eagleview.schema.app_profile import ClickSequence, Dismiss, GlobalState, SelectThenClick
from eagleview.schema.capability import Outcome
from eagleview.schema.predicates import AnyPredicate
from eagleview.surfaces.base import PredicateContext, Surface

POLL_MS = 150


async def pause() -> None:
    """The poll interval. The only sleep on the replay path."""
    await asyncio.sleep(POLL_MS / 1000)


def is_interstitial(state: GlobalState) -> bool:
    """A state a handler clears in place, after which the step carries on settling."""
    return isinstance(state.recover, Dismiss | SelectThenClick | ClickSequence)


SeenKind = Literal["hard", "outcome", "interstitial", "expect", "nothing"]


@dataclass(frozen=True)
class Seen:
    kind: SeenKind
    state: GlobalState | None = None
    outcome: Outcome | None = None


@dataclass(frozen=True)
class Before:
    """The screen just before an action, so a deadline can tell "nothing changed" from "something
    unrecognized is showing", and a true-before expect can wait for a real change."""

    fingerprint: str
    navigations: int
    expect_held: bool


class Watcher:
    def __init__(self, states: Sequence[GlobalState], outcomes: Sequence[Outcome]) -> None:
        self.hard = [s for s in states if not is_interstitial(s)]
        self.interstitials = [s for s in states if is_interstitial(s)]
        self.outcomes = list(outcomes)

    async def look(
        self,
        surface: Surface,
        expect: AnyPredicate | None,
        ctx: PredicateContext,
        *,
        with_outcomes: bool = True,
    ) -> Seen:
        outcomes = self.outcomes if with_outcomes else []
        preds: list[AnyPredicate] = [s.detect for s in self.hard]
        preds += [o.detect for o in outcomes]
        preds += [s.detect for s in self.interstitials]
        if expect is not None:
            preds.append(expect)
        held = iter(await surface.check_all(preds, ctx))
        for state in self.hard:
            if next(held):
                return Seen("hard", state=state)
        for outcome in outcomes:
            if next(held):
                return Seen("outcome", outcome=outcome)
        for state in self.interstitials:
            if next(held):
                return Seen("interstitial", state=state)
        if expect is not None and next(held):
            return Seen("expect")
        return Seen("nothing")

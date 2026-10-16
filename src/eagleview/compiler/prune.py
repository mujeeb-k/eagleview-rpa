"""Pruning: keep the shortest path through the screens, dropping the model's dead ends and retries.

Screen-changing steps (click, press) are edges in a graph whose nodes are state fingerprints. An
edge exists only where the action changed the screen and moved it to a different state; a
reload of the same state is a loop and never on a shortest path. The search runs from the first
step's state to the state the model declared success in.

Value-setting steps (fill, select, check) change no fingerprint, so they cannot be edges. They
are kept when their state is on the path, and only the last one per field in that state, which
drops retyping."""

import json
from collections import deque

from eagleview.compiler.segment import Seg

SCREEN_CHANGING = frozenset({"click", "press"})


def _field_key(s: Seg) -> str:
    g = s.grounded or {}
    first = (g.get("candidates") or [None])[0]
    return json.dumps([g.get("frame"), first], sort_keys=True)


def prune(steps: list[Seg]) -> list[Seg]:
    if not steps:
        return []
    edges = [
        (i, s)
        for i, s in enumerate(steps)
        if s.kind in SCREEN_CHANGING
        and s.changed
        and s.fp_before
        and s.fp_after
        and s.fp_before != s.fp_after
    ]
    start = steps[0].fp_before
    final = next(
        (s.fp_after for s in reversed(steps) if s.kind in SCREEN_CHANGING and s.fp_after),
        start,
    )
    # Shortest path by breadth-first search; among parallel edges, the latest attempt wins.
    out: dict[str, list[tuple[str, int]]] = {}
    for i, s in edges:
        assert s.fp_before is not None and s.fp_after is not None
        targets = out.setdefault(s.fp_before, [])
        targets[:] = [(to, j) for to, j in targets if to != s.fp_after]
        targets.append((s.fp_after, i))
    came: dict[str | None, tuple[str | None, int] | None] = {start: None}
    queue: deque[str | None] = deque([start])
    while queue:
        node = queue.popleft()
        if node == final:
            break
        for to, i in out.get(node or "", []):
            if to not in came:
                came[to] = (node, i)
                queue.append(to)
    if final not in came:
        return steps  # the graph is incomplete (a missed fingerprint): keep everything
    path: list[int] = []
    node: str | None = final
    while came[node] is not None:
        prev, i = came[node]  # type: ignore[misc]
        path.append(i)
        node = prev
    path.reverse()
    states = {start, *(steps[i].fp_after for i in path)}

    keep = set(path)
    last_value: dict[tuple[str | None, str], int] = {}
    for i, s in enumerate(steps):
        if s.kind not in SCREEN_CHANGING and s.fp_before in states:
            last_value[(s.fp_before, _field_key(s))] = i
    keep |= set(last_value.values())
    return [s for i, s in enumerate(steps) if i in keep]

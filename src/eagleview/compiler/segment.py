"""Segmenting: the trace's raw actions become candidate steps.

- a click on a field, then `fill_input` or a bound `type`: one `fill` step on the clicked field;
- a click on a select, then keys or typing: one `select` step, with the option the select showed;
- a click on a checkbox or radio: a value-setting `check` step;
- Return or Enter: a `press` step;
- any other click: a `click` step.

Looking (screenshot, zoom, wait), scrolling and mouse moves are dropped: replay scrolls targets
into view itself. A person's actions during a hand-over are steps like the model's."""

import re
from dataclasses import dataclass, field
from typing import Any, Literal

LOOKING = frozenset({"screenshot", "zoom", "wait", "mouse_move", "scroll"})
_TEXT_INPUTS = frozenset({None, "text", "password", "search", "email", "tel", "number"})
_SUBMIT_KEYS = frozenset({"return", "enter", "kp_enter"})
_PLACEHOLDER = re.compile(r"^\[([a-z][a-z0-9_]*)\]$")

Kind = Literal["click", "fill", "select", "check", "press"]


class CompileError(Exception):
    pass


@dataclass
class Seg:
    kind: Kind
    seq: int
    grounded: dict[str, Any] | None
    fp_before: str | None
    fp_after: str | None
    changed: bool
    appeared: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    value: str | None = None  # fill: a template or a literal
    option: str | None = None  # select: a template or a label
    key: str | None = None  # press
    irreversible: bool = False
    actor: str = "llm"
    rationale: str | None = None


@dataclass
class Mark:
    name: str
    kind: Literal["value", "table"]
    grounded: dict[str, Any]
    table: dict[str, Any] | None
    money: bool
    description: str


@dataclass
class Segmented:
    steps: list[Seg]
    marks: list[Mark]
    success: dict[str, Any] | None  # the declare_success grounding
    rationales: dict[int, str]


def bind(text: str | None, values: dict[str, str]) -> str | None:
    """A recorded value equal to an input's value, or its redaction placeholder, becomes that
    input's template, so the artifact never hard-codes this run's choice."""
    if text is None:
        return None
    for name, value in values.items():
        if text == value:
            return f"{{{{ inputs.{name} }}}}"
    placeholder = _PLACEHOLDER.fullmatch(text)
    if placeholder and placeholder.group(1) in values:
        return f"{{{{ inputs.{placeholder.group(1)} }}}}"
    return text


def _is_field(g: dict[str, Any]) -> bool:
    return g.get("role") == "textbox" or (
        g.get("tag") in ("input", "textarea") and g.get("input_type") in _TEXT_INPUTS
    )


def _is_check(g: dict[str, Any]) -> bool:
    return g.get("role") in ("checkbox", "radio") or g.get("input_type") in ("checkbox", "radio")


def segment(records: list[dict[str, Any]], values: dict[str, str]) -> Segmented:
    steps: list[Seg] = []
    marks: list[Mark] = []
    success: dict[str, Any] | None = None
    rationales: dict[int, str] = {}
    field_click: dict[str, Any] | None = None
    select: tuple[dict[str, Any], str | None] | None = None

    def seg(kind: Kind, r: dict[str, Any], g: dict[str, Any] | None, **extra: Any) -> Seg:
        fp = r.get("fingerprint") or {}
        decision = r.get("decision") or {}
        return Seg(
            kind=kind,
            seq=int(r["seq"]),
            grounded=g,
            fp_before=fp.get("before"),
            fp_after=fp.get("after"),
            changed=bool(r.get("changed")),
            appeared=list(r.get("appeared") or []),
            irreversible=bool(decision.get("irreversible")),
            actor=str(r.get("actor", "llm")),
            rationale=r.get("rationale"),
            **extra,
        )

    def flush_select() -> None:
        nonlocal select
        if select is not None:
            r, option = select
            steps.append(seg("select", r, r["grounded"], option=bind(option, values)))
            select = None

    for r in records:
        if r.get("rationale"):
            rationales[int(r["seq"])] = r["rationale"]
        tool, status = r["tool"], r.get("status")
        if tool == "mark_output" and status == "done":
            data = r.get("input") or {}
            marks.append(
                Mark(
                    name=str(data.get("name", "")),
                    kind="table" if data.get("kind") == "table" else "value",
                    grounded=r.get("grounded") or {},
                    table=r.get("table"),
                    money=bool(r.get("money")),
                    description=str(data.get("description", "")),
                )
            )
            continue
        if tool == "declare_success":
            success = r.get("grounded")
            break
        if status != "done" or tool in LOOKING:
            continue
        g: dict[str, Any] = r.get("grounded") or {}
        if tool in ("left_click", "double_click", "click"):
            flush_select()
            if g.get("tag") == "select":
                select = (r, r.get("select_value"))
            elif _is_field(g):
                field_click = r
            elif _is_check(g):
                steps.append(seg("check", r, g))
            else:
                field_click = None
                steps.append(seg("click", r, g))
        elif tool in ("fill_input", "type"):
            if select is not None:
                # A declared input typed into a select chooses that input's option: bound by
                # provenance, like a fill. (An open list commits its choice only when it closes,
                # so the value read just after typing can still show the old option.)
                bound = r.get("bound")
                chosen = f"{{{{ inputs.{bound} }}}}" if bound else r.get("select_value")
                select = (select[0], chosen or select[1])
                continue
            bound = r.get("bound")
            text = (r.get("input") or {}).get("text")
            value = f"{{{{ inputs.{bound} }}}}" if bound else bind(text, values)
            target = field_click["grounded"] if field_click is not None else g
            steps.append(seg("fill", field_click or r, target, value=value))
            steps[-1].changed = True
            field_click = None
        elif tool == "key":
            key = str((r.get("input") or {}).get("text", ""))
            fp = r.get("fingerprint") or {}
            moved = fp.get("before") != fp.get("after")
            if select is not None and not moved:
                select = (select[0], r.get("select_value") or select[1])
                continue
            flush_select()  # a key that changed the screen ends the choice and is a step itself
            if key.lower() in _SUBMIT_KEYS:
                steps.append(seg("press", r, None, key="Return"))
                field_click = None
    flush_select()
    if success is None:
        raise CompileError("the trace has no declare_success: discovery did not finish")
    return Segmented(steps, marks, success, rationales)

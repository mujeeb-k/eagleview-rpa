"""Assembling: pruned steps, marked outputs and the declared success become a draft `Capability`.

- Each step's target is its grounding's verified candidate locators, ranked as recorded.
- A click expects text that appeared after it (a heading first), plus the next step's target being
  present. A fill or select expects its own value. Text containing a redaction placeholder, or
  only digits, never becomes a predicate: predicates must not depend on inputs.
- Outputs fail closed: every output is `pii`. Money columns and values are decimals.
- Names here are mechanical; the naming pass (naming.py) proposes better ones."""

import re
from datetime import UTC, datetime
from typing import Any

from eagleview.compiler.segment import CompileError, Mark, Seg, Segmented
from eagleview.schema.app_profile import AppProfile
from eagleview.schema.capability import (
    AppRef,
    Capability,
    Discovery,
    FieldSpec,
    InputSpec,
    OutputSpec,
    Provenance,
    ScalarExtract,
    Step,
    TableExtract,
    TableSpec,
)
from eagleview.schema.common import Sensitivity
from eagleview.schema.locators import TargetSpec
from eagleview.schema.predicates import (
    AllOf,
    AnyPredicate,
    ElementArgs,
    ElementPresent,
    FieldValue,
    FieldValueArgs,
    OutputPresent,
    TextArgs,
    TextPresent,
)

_PLACEHOLDER = re.compile(r"\[[a-z][a-z0-9_]*\]")
_SLUG = re.compile(r"[^a-z0-9]+")


def slug(text: str, fallback: str = "item") -> str:
    s = _SLUG.sub("_", text.casefold()).strip("_")
    if not s or not s[0].isalpha():
        s = f"{fallback}_{s}" if s else fallback
    return s[:48].rstrip("_")


def usable(text: str) -> bool:
    """Text that can be a predicate: stable, not personal, not input-dependent."""
    return (
        bool(text)
        and len(text) <= 80
        and _PLACEHOLDER.search(text) is None
        and any(c.isalpha() for c in text)
    )


def target_of(g: dict[str, Any] | None, what: str) -> TargetSpec:
    if not g or not g.get("candidates"):
        raise CompileError(f"{what}: grounding verified no locator for this element")
    label = g.get("name") or g.get("text") or g.get("label")
    role = g.get("role") or g.get("tag") or "element"
    frame = "/".join(g.get("frame") or []) or "top"
    described = f"{label!r} {role} in frame {frame}" if label and usable(label) else None
    description = described or f"{role} in frame {frame}"
    return TargetSpec(
        description=description, frame=list(g.get("frame") or []), locators=g["candidates"]
    )


def _appeared_expect(s: Seg) -> TextPresent | None:
    texts = [a for a in s.appeared if usable(str(a.get("text", "")))]
    pick = next((a for a in texts if a.get("heading")), None) or (texts[0] if texts else None)
    if pick is None:
        return None
    frame = [pick["frame"]] if pick.get("frame") else []
    return TextPresent(text_present=TextArgs(frame=frame, text=str(pick["text"])))


def _expect(s: Seg, next_target: TargetSpec | None) -> AnyPredicate | None:
    if s.kind == "fill" and s.value is not None:
        return FieldValue(field_value=FieldValueArgs(target="self", equals=s.value))
    if s.kind == "select" and s.option is not None:
        return FieldValue(field_value=FieldValueArgs(target="self", equals=s.option))
    if s.kind == "check":
        return None
    preds: list[AnyPredicate] = []
    seen = _appeared_expect(s)
    if seen is not None:
        preds.append(seen)
    if next_target is not None:
        preds.append(ElementPresent(element_present=ElementArgs(target=next_target)))
    if not preds:
        return None
    return preds[0] if len(preds) == 1 else AllOf(all=preds)  # type: ignore[arg-type]


def _verb(s: Seg) -> str:
    return {
        "click": "click",
        "fill": "enter",
        "select": "choose",
        "check": "set",
        "press": "press",
    }[s.kind]


def _unique(base: str, taken: set[str]) -> str:
    name, n = base, 2
    while name in taken:
        name = f"{base}_{n}"
        n += 1
    taken.add(name)
    return name


def _output(mark: Mark) -> tuple[str, OutputSpec, Any]:
    name = slug(mark.name, "output")
    if mark.kind == "value":
        target = target_of(mark.grounded, f"output {name}")
        spec = OutputSpec(
            type="decimal" if mark.money else "string",
            scale=2 if mark.money else None,
            sensitivity=Sensitivity.pii,
        )
        return name, spec, ScalarExtract(target=target, parse="money_us" if mark.money else "text")
    table = mark.table or {}
    caption = table.get("caption")
    headers = [h for h in table.get("headers") or [] if h]
    if not caption or not headers:
        raise CompileError(f"output {name}: the marked table has no caption or headers")
    money = set(table.get("money") or [])
    columns: dict[str, str] = {}
    taken: set[str] = set()
    for h in headers:
        columns[_unique(slug(h, "column"), taken)] = h
    items = {
        k: FieldSpec(type="decimal", scale=2) if h in money else FieldSpec(type="string")
        for k, h in columns.items()
    }
    spec = OutputSpec(type="array", sensitivity=Sensitivity.pii, items=items)
    extract = TableExtract(
        table=TableSpec(
            anchor_text=caption,
            frame=list(mark.grounded.get("frame") or []),
            columns=columns,
            parse={k: "money_us" for k, h in columns.items() if h in money},
        )
    )
    return name, spec, extract


def assemble(
    seg: Segmented,
    kept: list[Seg],
    *,
    goal: str,
    inputs: dict[str, InputSpec],
    profile: AppProfile,
    run_id: str,
    model: str,
) -> Capability:
    targets = [
        None if s.kind == "press" else target_of(s.grounded, f"step {i + 1} ({s.kind})")
        for i, s in enumerate(kept)
    ]
    taken: set[str] = set()
    steps: list[Step] = []
    for i, s in enumerate(kept):
        label = (s.grounded or {}).get("name") or (s.grounded or {}).get("text") or ""
        if not usable(str(label)):
            label = (s.grounded or {}).get("anchor_left") or s.kind
        sid = _unique(slug(f"{_verb(s)}_{label}", "step"), taken)
        next_target = targets[i + 1] if i + 1 < len(kept) else None
        action: dict[str, Any] = {"type": "click"}
        if s.kind == "fill":
            action = {"type": "fill", "value": s.value or ""}
        elif s.kind == "select":
            action = {"type": "select", "option": s.option or ""}
        elif s.kind == "press":
            action = {"type": "press", "key": s.key or "Return"}
        irreversible = s.irreversible
        target = targets[i]
        steps.append(
            Step.model_validate(
                {
                    "id": sid,
                    "intent": s.rationale,
                    "action": action,
                    "target": target.model_dump(mode="json") if target is not None else None,
                    "risk": "irreversible" if irreversible else "read",
                    "idempotent": not irreversible,
                    "requires_approval": irreversible,
                    "expect": None
                    if (e := _expect(s, next_target)) is None
                    else e.model_dump(mode="json", by_alias=True),
                }
            )
        )
    outputs: dict[str, OutputSpec] = {}
    extract: dict[str, Any] = {}
    for mark in seg.marks:
        name, spec, how = _output(mark)
        name = _unique(name, set(outputs))
        outputs[name] = spec
        extract[name] = how
    if extract:
        steps.append(
            Step(id=_unique("read_outputs", taken), action={"type": "extract"}, extract=extract)  # type: ignore[arg-type]
        )
    success = _success(seg.success, kept, list(outputs))
    risk = "irreversible" if any(s.risk == "irreversible" for s in steps) else "read_only"
    return Capability(
        schema_version="1.0",
        id=f"task.{slug(goal, 'task')}"[:60].rstrip("_"),
        version="1.0.0",
        title=goal[:120],
        description=goal,
        status="draft",
        app=AppRef(profile=profile.id, versions=profile.versions),
        risk=risk,  # type: ignore[arg-type]
        inputs=inputs,
        outputs=outputs,
        steps=steps,
        success=success,  # type: ignore[arg-type]
        provenance=Provenance(
            discovered_by=Discovery(run_id=run_id, model=model, at=datetime.now(UTC))
        ),
    )


def _success(declared: dict[str, Any] | None, kept: list[Seg], outputs: list[str]) -> AnyPredicate:
    preds: list[AnyPredicate] = []
    text = None
    frame: list[str] = []
    if declared:
        candidate = declared.get("text") or declared.get("name")
        if candidate and usable(str(candidate)):
            text, frame = str(candidate), list(declared.get("frame") or [])
    if text is None:
        for s in reversed(kept):
            seen = _appeared_expect(s)
            if seen is not None:
                text, frame = seen.text_present.text, seen.text_present.frame
                break
    if text is not None:
        preds.append(TextPresent(text_present=TextArgs(frame=frame, text=text)))
    preds += [OutputPresent(output_present=name) for name in outputs]
    if not preds:
        raise CompileError("no stable success signal: nothing usable was declared or appeared")
    return preds[0] if len(preds) == 1 else AllOf(all=preds)  # type: ignore[arg-type]

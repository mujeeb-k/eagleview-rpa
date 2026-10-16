"""The naming pass: Claude, with structured output, proposes names and descriptions for a compiled
draft. It may not alter locators, predicates or step order, and it may never lower a risk.

Accepted:
- the capability id, title and description;
- step ids, intents, and a raise from `read` to `write_staged`;
- output and item-field names, and output descriptions;
- input descriptions, examples and patterns. A pattern must match the discovery value.

Anything that fails validation keeps the mechanical name."""

import re
from typing import Any

import anthropic

from eagleview.discovery.client import structured
from eagleview.discovery.meter import Meter
from eagleview.schema.capability import Capability

_ID = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_SLUG = re.compile(r"^[a-z][a-z0-9_]{0,47}$")

_FIELDS = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"current": {"type": "string"}, "name": {"type": "string"}},
        "required": ["current", "name"],
        "additionalProperties": False,
    },
}
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "capability_id": {"type": "string"},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "id": {"type": "string"},
                    "intent": {"type": "string"},
                    "risk": {"type": "string", "enum": ["read", "write_staged"]},
                },
                "required": ["index", "id", "intent", "risk"],
                "additionalProperties": False,
            },
        },
        "outputs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "current": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "fields": _FIELDS,
                },
                "required": ["current", "name", "description", "fields"],
                "additionalProperties": False,
            },
        },
        "inputs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "pattern": {"type": "string"},
                    "example": {"type": "string"},
                },
                "required": ["name", "description", "pattern", "example"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["capability_id", "title", "description", "steps", "outputs", "inputs"],
    "additionalProperties": False,
}

PROMPT = """You are naming a compiled automation for a legacy bank teller application, so that a \
reviewer and a calling agent can understand it. Do not change what it does.

Goal it was discovered from: {goal}

Inputs (name, type, the value used during discovery):
{inputs}

Steps, in order (index, action, target, rationale recorded during discovery):
{steps}

Outputs (current name, kind, item fields with the screen's column headers):
{outputs}

Propose:
- capability_id: dotted lowercase, domain then verb_object, e.g. member.lookup_share_balances.
- title: one short sentence. description: two or three sentences on what it does and returns, \
and whether it changes anything.
- For every step: a snake_case id naming what the step does (open_member_inquiry, \
enter_member_id), unique; a one-sentence intent; risk "write_staged" only if the step stages a \
change without committing it (for example validating a form that a later step posts), else "read".
- For every output and item field: a snake_case name for what it holds, and a description.
- For every input: a description, an illustrative example, and a regular expression for the \
shape of the value. Other institutions on the same product may use different lengths, so \
describe the kind of value rather than this example's exact length (for a member number, a \
range such as 4 to 10 digits). The discovery value must match it."""


def _summary(cap: Capability, values: dict[str, str]) -> dict[str, str]:
    inputs = "\n".join(f"- {n}: {s.type}, {values.get(n, '')!r}" for n, s in cap.inputs.items())
    steps = []
    for i, st in enumerate(cap.steps):
        what = st.target.description if st.target else ""
        steps.append(f"{i}. {st.action.type} {what} — {st.intent or ''}")
    outputs = []
    for name, spec in cap.outputs.items():
        fields = ""
        if spec.items:
            extract = next(
                (e for s in cap.steps for k, e in (s.extract or {}).items() if k == name), None
            )
            columns = getattr(getattr(extract, "table", None), "columns", {})
            fields = ", ".join(f"{k} ({columns.get(k, k)})" for k in spec.items)
        outputs.append(f"- {name}: {spec.type}" + (f"; fields: {fields}" if fields else ""))
    return {"inputs": inputs, "steps": "\n".join(steps), "outputs": "\n".join(outputs)}


async def propose_names(
    client: anthropic.AsyncAnthropic,
    model: str,
    meter: Meter,
    run_id: str,
    cap: Capability,
    goal: str,
    values: dict[str, str],
) -> dict[str, Any]:
    meter.before_request(run_id)
    response = await client.messages.create(
        model=model,
        max_tokens=8000,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": PROMPT.format(goal=goal, **_summary(cap, values))}],
    )
    meter.after_response(run_id, model, response, "naming")
    return structured(response, "naming")


def apply_names(
    cap: Capability, names: dict[str, Any], values: dict[str, str]
) -> tuple[Capability, list[str]]:
    """The draft with every valid proposal applied, and what was rejected (for the review)."""
    data = cap.model_dump(mode="json", by_alias=True, exclude_none=True)
    rejected: list[str] = []

    cid = str(names.get("capability_id", ""))
    if _ID.fullmatch(cid):
        data["id"] = cid
    else:
        rejected.append(f"capability_id {cid!r}")
    for key in ("title", "description"):
        if str(names.get(key, "")).strip():
            data[key] = str(names[key]).strip()

    taken: set[str] = set()
    proposals = {int(p["index"]): p for p in names.get("steps", []) if "index" in p}
    for i, step in enumerate(data["steps"]):
        p = proposals.get(i)
        if p is None:
            taken.add(step["id"])
            continue
        sid = str(p.get("id", ""))
        if _SLUG.fullmatch(sid) and sid not in taken:
            step["id"] = sid
        else:
            rejected.append(f"step id {sid!r}")
        taken.add(step["id"])
        if str(p.get("intent", "")).strip():
            step["intent"] = str(p["intent"]).strip()
        if p.get("risk") == "write_staged" and step.get("risk", "read") == "read":
            step["risk"] = "write_staged"  # raise only; never lower
    _rename_outputs(data, names.get("outputs", []), rejected)
    for p in names.get("inputs", []):
        name = p.get("name")
        spec = data["inputs"].get(name)
        if spec is None:
            continue
        if str(p.get("description", "")).strip():
            spec["description"] = str(p["description"]).strip()
        pattern = str(p.get("pattern", ""))
        try:
            ok = bool(pattern) and re.fullmatch(pattern, values.get(name, "")) is not None
        except re.error:
            ok = False
        if ok:
            spec["pattern"] = pattern
            example = str(p.get("example", ""))
            if example and re.fullmatch(pattern, example):
                spec["example"] = example
        else:
            rejected.append(f"input pattern {pattern!r} for {name}")
    risks = {s.get("risk", "read") for s in data["steps"]}
    data["risk"] = (
        "irreversible"
        if "irreversible" in risks
        else "write_staged"
        if "write_staged" in risks
        else "read_only"
    )
    return Capability.model_validate(data), rejected


def _rename_outputs(
    data: dict[str, Any], proposals: list[dict[str, Any]], rejected: list[str]
) -> None:
    for p in proposals:
        old, new = str(p.get("current", "")), str(p.get("name", ""))
        if old not in data["outputs"]:
            continue
        if not _SLUG.fullmatch(new) or (new != old and new in data["outputs"]):
            rejected.append(f"output name {new!r}")
            new = old
        spec = data["outputs"].pop(old)
        if str(p.get("description", "")).strip():
            spec["description"] = str(p["description"]).strip()
        data["outputs"][new] = spec
        for step in data["steps"]:
            extract = step.get("extract") or {}
            if old in extract:
                extract[new] = extract.pop(old)
                _rename_fields(spec, extract[new], p.get("fields", []), rejected)
        _rename_predicates(data["success"], old, new)


def _rename_fields(
    spec: dict[str, Any], extract: dict[str, Any], fields: list[dict[str, Any]], rejected: list[str]
) -> None:
    items = spec.get("items")
    table = extract.get("table")
    if not items or not table:
        return
    for f in fields:
        old, new = f.get("current"), str(f.get("name", ""))
        if old not in items or new == old:
            continue
        if not _SLUG.fullmatch(new) or new in items:
            rejected.append(f"field name {new!r}")
            continue
        items[new] = items.pop(old)
        table["columns"][new] = table["columns"].pop(old)
        if old in table.get("parse", {}):
            table["parse"][new] = table["parse"].pop(old)


def _rename_predicates(pred: Any, old: str, new: str) -> None:
    if isinstance(pred, dict):
        if pred.get("output_present") == old:
            pred["output_present"] = new
        for value in pred.values():  # pyright: ignore[reportUnknownVariableType]
            _rename_predicates(value, old, new)
    elif isinstance(pred, list):
        for item in pred:  # pyright: ignore[reportUnknownVariableType]
            _rename_predicates(item, old, new)

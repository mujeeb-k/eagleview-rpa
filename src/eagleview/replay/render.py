"""Substituting `{{ inputs.x }}` into a capability before it runs. Templates may appear in action
values, targets and predicates alike, so `field_value: {equals: "{{ inputs.member_id }}"}`
compares against the member number, not against the template text."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, cast

from pydantic import TypeAdapter

from eagleview.schema.capability import Capability, Outcome, Step
from eagleview.schema.predicates import AnyPredicate, Predicate
from eagleview.schema.templating import render_template

_PREDICATE: TypeAdapter[AnyPredicate] = TypeAdapter(Predicate)


@dataclass(frozen=True)
class Rendered:
    steps: list[Step]
    outcomes: list[Outcome]
    success: AnyPredicate


def _text(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")
    return "" if value is None else str(value)


def _render(tree: object, values: Mapping[str, Mapping[str, object]]) -> object:
    if isinstance(tree, str):
        return render_template(tree, values) if "{{" in tree else tree
    if isinstance(tree, dict):
        items = cast(dict[str, object], tree).items()
        return {k: _render(v, values) for k, v in items}
    if isinstance(tree, list):
        return [_render(v, values) for v in cast(list[object], tree)]
    return tree


def _dump(model: Any) -> object:
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


def render(capability: Capability, inputs: Mapping[str, object]) -> Rendered:
    """Declared inputs that were not given render as the empty string."""
    values: dict[str, Mapping[str, object]] = {
        "inputs": {name: _text(inputs.get(name)) for name in capability.inputs}
    }
    steps = [Step.model_validate(_render(_dump(s), values)) for s in capability.steps]
    outcomes = [Outcome.model_validate(_render(_dump(o), values)) for o in capability.outcomes]
    success = _PREDICATE.validate_python(
        _render(_PREDICATE.dump_python(capability.success, mode="json", by_alias=True), values)
    )
    return Rendered(steps, outcomes, success)

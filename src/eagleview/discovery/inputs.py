"""Before the loop: turn the goal into a typed input schema plus values, with structured output.
"look up member 10482 and read their share balances" -> member_id: string, "10482"."""

import json
import re
from dataclasses import dataclass
from typing import Any

import anthropic

from eagleview.discovery.meter import Meter
from eagleview.schema.capability import InputSpec
from eagleview.schema.common import Sensitivity

_SLUG = re.compile(r"^[a-z][a-z0-9_]{0,40}$")

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "inputs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string", "enum": ["string", "integer", "decimal", "boolean"]},
                    "value": {"type": "string"},
                    "description": {"type": "string"},
                    "sensitivity": {"type": "string", "enum": ["none", "identifier", "pii"]},
                },
                "required": ["name", "type", "value", "description", "sensitivity"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["inputs"],
    "additionalProperties": False,
}

PROMPT = """A bank teller automation is being authored from this goal:

<goal>{goal}</goal>

List the values the goal supplies that a teller would type into the application: identifiers,
names, amounts, choices. Name each in snake_case for what it is in general (member_id, not
member_10482). Give the value exactly as written in the goal. Type is string unless it is
arithmetic (decimal for money, integer for counts); identifiers are strings, even when they are
digits. Sensitivity: identifier for account or member numbers, pii for names, addresses, dates
of birth, contact details, none otherwise. Leave out anything the goal asks to read rather than
supply. Return an empty list when the goal supplies nothing."""


@dataclass(frozen=True)
class GoalInputs:
    specs: dict[str, InputSpec]
    values: dict[str, str]


async def extract_inputs(
    client: anthropic.AsyncAnthropic, model: str, goal: str, meter: Meter, run_id: str
) -> GoalInputs:
    meter.before_request(run_id)
    response = await client.messages.create(
        model=model,
        max_tokens=4000,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": PROMPT.format(goal=goal)}],
    )
    meter.after_response(run_id, model, response, "input_extraction")
    text = next(b.text for b in response.content if b.type == "text")
    specs: dict[str, InputSpec] = {}
    values: dict[str, str] = {}
    for item in json.loads(text)["inputs"]:
        name = str(item["name"])
        if not _SLUG.fullmatch(name) or name in specs:
            raise ValueError(f"input name {name!r} is not a usable snake_case slug")
        specs[name] = InputSpec(
            type=item["type"],
            sensitivity=Sensitivity(item["sensitivity"]),
            description=item["description"] or None,
        )
        values[name] = str(item["value"])
    return GoalInputs(specs, values)

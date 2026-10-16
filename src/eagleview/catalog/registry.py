"""The registry: approved capabilities, each mapped to a tool definition an agent can call.

Only `approved` capabilities in `capabilities/` are listed: never drafts and never merely
verified ones. Irreversible capabilities are left out, because an agent's call is an unattended
run, and an unattended run can only stop at an approval gate. Each tool has:
- a snake_case name from the capability id;
- a description built from the title, the description, the business outcomes, the risk and
  the output shape;
- the input JSON Schema, patterns included, so a bad value is caught before anything runs."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eagleview.artifacts.loader import CAPABILITIES
from eagleview.artifacts.yaml_io import load_capability
from eagleview.schema.capability import Capability, OutputSpec

_JSON_TYPE = {"string": "string", "integer": "integer", "decimal": "string", "boolean": "boolean"}


@dataclass(frozen=True)
class Entry:
    capability: Capability
    path: Path

    @property
    def tool_name(self) -> str:
        return self.capability.id.replace(".", "_")

    @property
    def description(self) -> str:
        cap = self.capability
        outputs = "; ".join(f"{n}: {_shape(s)}" for n, s in cap.outputs.items()) or "nothing"
        parts = [
            cap.title.rstrip(".") + ".",
            " ".join(cap.description.split()),
            f"Returns {outputs}.",
        ]
        if cap.outcomes:
            listed = "; ".join(f"{o.code} ({o.message.rstrip('.')})" for o in cap.outcomes)
            parts.append(f"Business outcomes, returned as status 'outcome', not errors: {listed}.")
        parts.append(
            f"Risk: {cap.risk}. Runs deterministically against the teller application and "
            "returns a RunResult: status success, outcome, rejected, failed or needs_human. "
            "Money is a decimal string."
        )
        return " ".join(parts)

    @property
    def input_schema(self) -> dict[str, Any]:
        props: dict[str, Any] = {}
        for name, spec in self.capability.inputs.items():
            prop: dict[str, Any] = {"type": _JSON_TYPE[spec.type]}
            if spec.pattern:
                prop["pattern"] = spec.pattern
            if spec.enum:
                prop["enum"] = list(spec.enum)
            if spec.description:
                prop["description"] = spec.description
            props[name] = prop
        required = [n for n, s in self.capability.inputs.items() if s.required]
        return {
            "type": "object",
            "properties": props,
            "required": required,
            "additionalProperties": False,
        }


def _shape(spec: OutputSpec) -> str:
    if spec.items:
        fields = ", ".join(f"{k} ({v.type})" for k, v in spec.items.items())
        return f"a list of {{{fields}}}"
    return spec.type


def approved(root: Path = CAPABILITIES) -> list[Entry]:
    """Approved, non-irreversible capabilities. A file whose hash no longer matches is refused
    by the loader, so an edited capability is never served."""
    entries: list[Entry] = []
    for path in sorted(root.glob("*.yaml")):
        cap = load_capability(path)
        if cap.status == "approved" and cap.risk != "irreversible":
            entries.append(Entry(cap, path))
    return entries


def anthropic_tools(entries: list[Entry]) -> list[dict[str, Any]]:
    return [
        {"name": e.tool_name, "description": e.description, "input_schema": e.input_schema}
        for e in entries
    ]

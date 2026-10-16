"""Outcome probing: learn what a legitimate "no" looks like, such as member not found.

1. Claude proposes three input sets that are valid in shape but unlikely to exist. Each must pass
   the capability's input schema and differ from the discovery values.
2. Each runs as a probe replay: no desk and no approvals, so it can never commit. When it ends on
   a screen nothing recognized, replay writes the visible text, redacted, to `probe.json`.
3. Claude classifies each distinct screen: a business outcome or not, and if so a code, a message
   and a regex. A regex that does not compile, does not match the captured text, or contains a
   redaction placeholder or a probe value is rejected.
4. The probe runs again with the outcome added. Only if it now returns that outcome is the
   outcome kept, as `probed_verified`. A failed confirm drops it."""

import json
import re
from pathlib import Path
from typing import Any

import anthropic

from eagleview.discovery.client import StructuredRefused, structured
from eagleview.discovery.meter import Meter
from eagleview.replay.preflight import check_inputs
from eagleview.schema.capability import Capability, Outcome
from eagleview.schema.common import Source
from eagleview.schema.predicates import PatternInFrameArgs, TextMatches

_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,40}$")
_PLACEHOLDER = re.compile(r"\[[a-z][a-z0-9_]*\]")

PROPOSE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "probes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "values": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "value": {"type": "string"},
                            },
                            "required": ["name", "value"],
                            "additionalProperties": False,
                        },
                    },
                    "why": {"type": "string"},
                },
                "required": ["values", "why"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["probes"],
    "additionalProperties": False,
}

CLASSIFY: dict[str, Any] = {
    "type": "object",
    "properties": {
        "business_outcome": {"type": "boolean"},
        "code": {"type": "string"},
        "message": {"type": "string"},
        "pattern": {"type": "string"},
        "frame": {"type": "string"},
    },
    "required": ["business_outcome", "code", "message", "pattern", "frame"],
    "additionalProperties": False,
}

PROPOSE_PROMPT = """This is software testing on a synthetic training system: a fictional credit \
union's teller application filled with generated test data. An automation for it takes these \
inputs:
{inputs}
During its recording it ran with: {values}

To learn how the application reports a record that does not exist (for example "no member \
found"), propose 3 test input sets in the same format (each value matches its pattern) that are \
unlikely to belong to any test record, such as 99999 for a five-digit number. Never reuse the \
recorded values. Keep any other inputs as recorded."""

CLASSIFY_PROMPT = """An automation for a bank teller application ran with input values that \
probably do not exist, and stopped at step "{step}" on a screen it did not recognize. The \
visible text was (frame: text). Values the automation typed are shown as [name] placeholders.

{texts}

Is this a legitimate business answer, such as "no such member" or "not allowed", rather than an \
error or a broken page? If so give:
- code: UPPER_SNAKE_CASE, e.g. MEMBER_NOT_FOUND;
- message: one sentence for a calling agent;
- pattern: a regular expression that matches the message's wording on screen. Use (?i) for case. \
It must not contain the typed values or the [placeholders], so it matches for any input;
- frame: the frame that shows it (empty for the top page)."""


async def propose_probes(
    client: anthropic.AsyncAnthropic,
    model: str,
    meter: Meter,
    run_id: str,
    cap: Capability,
    values: dict[str, str],
) -> list[dict[str, str]]:
    inputs = "\n".join(
        f"- {n}: {s.type}, pattern {s.pattern or '(none)'}" for n, s in cap.inputs.items()
    )
    meter.before_request(run_id)
    response = await client.messages.create(
        model=model,
        max_tokens=4000,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": PROPOSE}},
        messages=[
            {
                "role": "user",
                "content": PROPOSE_PROMPT.format(inputs=inputs, values=json.dumps(values)),
            }
        ],
    )
    meter.after_response(run_id, model, response, "probe_proposal")
    try:
        proposed = structured(response, "probe proposal")["probes"]
    except StructuredRefused:
        proposed = []
    out: list[dict[str, str]] = []
    for probe in proposed:
        candidate = dict(values) | {str(v["name"]): str(v["value"]) for v in probe["values"]}
        _, errors = check_inputs(cap, candidate)
        if not errors and candidate != values and candidate not in out:
            out.append(candidate)
    if not out:
        out = fallback_probes(cap, values)
    return out[:3]


def fallback_probes(cap: Capability, values: dict[str, str]) -> list[dict[str, str]]:
    """When Claude proposes nothing usable: for each digit-string input, the same length of all
    nines, then all zeros, kept only if the schema accepts it. Deterministic and cheap."""
    out: list[dict[str, str]] = []
    for name, value in values.items():
        if not value.isdigit():
            continue
        for digit in "90":
            candidate = dict(values) | {name: digit * len(value)}
            _, errors = check_inputs(cap, candidate)
            if not errors and candidate != values and candidate not in out:
                out.append(candidate)
    return out


def read_probe(run_dir: Path) -> dict[str, Any] | None:
    path = run_dir / "probe.json"
    return json.loads(path.read_text()) if path.is_file() else None


async def classify(
    client: anthropic.AsyncAnthropic,
    model: str,
    meter: Meter,
    run_id: str,
    state: dict[str, Any],
    probe_values: dict[str, str],
) -> tuple[Outcome | None, str]:
    """An outcome to confirm, or None and why."""
    texts: list[dict[str, Any]] = state.get("texts", [])
    listing = "\n".join(f"{t.get('frame') or 'top'}: {t['text']}" for t in texts)
    meter.before_request(run_id)
    response = await client.messages.create(
        model=model,
        max_tokens=4000,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": CLASSIFY}},
        messages=[
            {
                "role": "user",
                "content": CLASSIFY_PROMPT.format(step=state.get("step_id"), texts=listing),
            }
        ],
    )
    meter.after_response(run_id, model, response, "probe_classification")
    try:
        data = structured(response, "probe classification")
    except StructuredRefused as exc:
        return None, str(exc)
    if not data.get("business_outcome"):
        return None, "not a business outcome"
    code, pattern = str(data.get("code", "")), str(data.get("pattern", ""))
    frame = str(data.get("frame") or "")
    if not _CODE.fullmatch(code):
        return None, f"bad outcome code {code!r}"
    unescaped = pattern.replace("\\", "")
    if _PLACEHOLDER.search(unescaped) or any(v and v in unescaped for v in probe_values.values()):
        return None, "the pattern depends on the input"
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return None, f"the pattern does not compile: {exc}"
    scoped = [str(t["text"]) for t in texts if (t.get("frame") or "") == frame] or [
        str(t["text"]) for t in texts
    ]
    if not any(rx.search(t) for t in scoped):
        return None, "the pattern does not match the captured screen"
    # Input-independent: it must still match with every typed value replaced by another one.
    if not any(rx.search(_PLACEHOLDER.sub("Q7Z0Q", t)) for t in scoped):
        return None, "the pattern only matches this probe's values"
    detect = TextMatches(
        text_matches=PatternInFrameArgs(frame=[frame] if frame else [], pattern=pattern)
    )
    message = str(data.get("message", "")).strip() or code
    return Outcome(code=code, detect=detect, message=message, source=Source.probed), "ok"

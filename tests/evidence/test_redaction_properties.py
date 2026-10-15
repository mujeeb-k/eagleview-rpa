"""Property: generated PII never survives `EvidenceWriter`.

Registered values (inputs, outputs, secrets) must vanish from every string whatever surrounds
them: mid-string, in dict keys, in any letter case, next to non-ASCII text. Bare numbers outside
declared fields are deliberately left alone (see test_writer's RunResult reload test).
Pattern-only values (SSN, card, email, phone, labelled DOB) are embedded between separators,
because a regex cannot tell an SSN glued to other digits from a longer number; that limit is
documented in docs/DECISIONS.md."""

import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from eagleview.artifacts.yaml_io import load_capability, load_policy
from eagleview.evidence.writer import EvidenceWriter
from eagleview.safety.redaction import MIN_NAME_TOKEN_LENGTH, Redactor
from eagleview.schema.common import Sensitivity
from eagleview.schema.events import STRUCTURAL_FIELDS
from tests.fixtures import CAPABILITY, POLICY

CAP = load_capability(CAPABILITY)
PATTERNS = load_policy(POLICY).sensitivity_patterns

# Letters whose upper and lower case are one character each, so case changes keep lengths.
NAME_LETTERS = "abcdefghijklmnopqrstuvwxyzéñöçåø"
FILLER = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ äöüßæ漢字ÉÑ", max_size=12
)
SEPARATORS = st.sampled_from([" ", ", ", "; ", " | ", "\n", "\t", " — ", " (", ") ", " « ", "/ "])


def _luhn_complete(body: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(body)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return body + str((10 - total % 10) % 10)


digits = st.text(alphabet="0123456789", min_size=1, max_size=1)
ssns = st.from_regex(r"\A\d{3}-\d{2}-\d{4}\Z")
cards = st.builds(
    lambda body, sep: sep.join(re.findall(".{1,4}", _luhn_complete(body))),
    st.from_regex(r"\A[3-6]\d{14}\Z"),
    st.sampled_from(["", " ", "-"]),
)
emails = st.builds(
    lambda user, host: f"{user}@{host}.example",
    st.from_regex(r"\A[a-z][a-z0-9._]{1,10}[a-z0-9]\Z"),
    st.from_regex(r"\A[a-z]{2,10}\Z"),
)
phones = st.builds(
    lambda a, b, c: f"({a}) {b}-{c}",
    st.from_regex(r"\A\d{3}\Z"),
    st.from_regex(r"\A\d{3}\Z"),
    st.from_regex(r"\A\d{4}\Z"),
)
dobs = st.builds(
    lambda m, d, y: f"DOB: {m}/{d}/{y}",
    st.integers(1, 12),
    st.integers(1, 28),
    st.integers(1930, 2008),
)
member_ids = st.from_regex(r"\A[1-9]\d{4,7}\Z")
name_words = st.text(alphabet=NAME_LETTERS, min_size=MIN_NAME_TOKEN_LENGTH, max_size=10)
names = st.builds(lambda last, first: f"{last.upper()}, {first.upper()}", name_words, name_words)
secrets = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!#%&*-_",
    min_size=8,
    max_size=20,
)


@dataclass
class Pii:
    member_id: str
    name: str
    secret: str
    ssn: str
    card: str
    email: str
    phone: str
    dob: str


pii_records = st.builds(Pii, member_ids, names, secrets, ssns, cards, emails, phones, dobs)
casings = st.sampled_from([str.upper, str.lower, str.title, lambda s: s])


def _embed(draw: st.DrawFn, value: str) -> str:
    return draw(FILLER) + draw(SEPARATORS) + value + draw(SEPARATORS) + draw(FILLER)


@st.composite
def payloads(draw: st.DrawFn) -> tuple[Pii, dict[str, object]]:
    pii = draw(pii_records)
    case = draw(casings)
    pattern_values = [pii.ssn, pii.card, pii.email, pii.phone, pii.dob]
    # Registered values may be glued to anything; pattern values sit between separators.
    glued = draw(FILLER) + case(pii.name) + draw(FILLER) + pii.member_id + draw(FILLER)
    event: dict[str, object] = {
        "screen": " ".join(_embed(draw, v) for v in draw(st.permutations(pattern_values))),
        "glued": glued,
        "typed": draw(FILLER) + pii.secret + draw(FILLER),
        "inputs": {"member_id": pii.member_id},
        "outputs": {"member_name": pii.name},
        "nested": [{case(pii.name): [pii.email, {"k": pii.phone}]}, (pii.ssn,)],
        f"key {pii.member_id}": "x",
        "first_name_token": case(pii.name.split(", ")[1]),
    }
    return pii, event


def _scan_text(run_dir: Path) -> str:
    """Everything written, with the writer's own structural fields removed from events."""
    parts: list[str] = []
    for path in sorted(run_dir.rglob("*")):
        if path.suffix == ".jsonl":
            for line in path.read_text().splitlines():
                record = json.loads(line)
                parts.append(
                    json.dumps(
                        {k: v for k, v in record.items() if k not in STRUCTURAL_FIELDS},
                        ensure_ascii=False,
                    )
                )
        elif path.suffix in (".json", ".html"):
            text = path.read_text()
            parts.append(text)
            if path.suffix == ".json":
                parts.append(json.dumps(json.loads(text), ensure_ascii=False))
    return "\n".join(parts)


def _token_pattern(token: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![^\W\d_]){re.escape(token)}(?![^\W\d_])", re.IGNORECASE)


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(payloads())
def test_generated_pii_never_survives_the_writer(sample: tuple[Pii, dict[str, object]]) -> None:
    pii, event = sample
    redactor = Redactor(PATTERNS)
    redactor.use_capability(CAP)
    redactor.register_inputs({"member_id": pii.member_id})
    redactor.register_outputs({"member_name": pii.name})
    redactor.register_secret(pii.secret)
    with tempfile.TemporaryDirectory() as root:
        writer = EvidenceWriter(Path(root), "r_prop", redactor)
        writer.event("step.observe", "automation", **event)
        writer.write_json("summary", {"outputs": event["outputs"], "copy": event})
        writer.dom_snapshot("page", f'<td>{event["screen"]}</td><input value="{event["glued"]}">')
        written = _scan_text(writer.run_dir)

    folded = written.casefold()
    for value in (
        pii.member_id,
        pii.name,
        pii.secret,
        pii.ssn,
        pii.card,
        pii.email,
        pii.phone,
        pii.dob,
    ):
        assert value.casefold() not in folded, value
    # Placeholders such as "[member_name]" are the writer's own labels, not leaked text.
    unlabelled = re.sub(r"\[[a-z0-9_.]+\]", " ", written)
    for token in re.findall(r"[^\W\d_]+", pii.name):
        assert not _token_pattern(token).search(unlabelled), token


@settings(max_examples=60, deadline=None)
@given(pii_records)
def test_screenshot_boxes_holding_pii_are_always_flagged(pii: Pii) -> None:
    """The writer paints every box `is_sensitive` flags; every PII kind must be flagged alone."""
    redactor = Redactor(PATTERNS)
    redactor.register(pii.member_id, Sensitivity.identifier, "member_id")
    redactor.register(pii.name, Sensitivity.pii, "member_name", is_name=True)
    for text in (pii.member_id, pii.name, pii.ssn, pii.card, pii.email, pii.phone, pii.dob):
        assert redactor.is_sensitive(text), text
    for token in re.findall(r"[^\W\d_]+", pii.name):
        assert redactor.is_sensitive(token), token

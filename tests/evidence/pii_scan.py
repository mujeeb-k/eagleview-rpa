"""The PII scan over /evidence: what must never appear in any text file the evidence scripts wrote.

- the policy's sensitivity patterns (SSN, card numbers, email, phone, labelled DOB and account
  numbers);
- every seeded member's name, member number, phone, email and street, at both tenants;
- the training passwords from `.env`, and anything shaped like an Anthropic key.

JSON and JSONL files are scanned by their string values only, so a timing of 10234 ms is never
mistaken for a member number; other files use word boundaries. `README.md` files and copied
artifacts may name the demo members from PLAN §0, and only them: an artifact's input `example`
is the synthetic demo value by design (PLAN §3.2)."""

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from eagleview.artifacts.yaml_io import load_policy
from eagleview.safety.redaction import luhn_ok
from mock_bank.config import all_tenants
from mock_bank.seed import generate

REPO = Path(__file__).resolve().parents[2]
TEXT = {".json", ".jsonl", ".html", ".yaml", ".md", ".txt"}
# The PLAN's named synthetic members (each tenant's demo member and drill member), which docs and
# artifact examples may name. Read from tenant config, not hard-coded.
README_ALLOWED = {number for t in all_tenants() for number in (t.demo_member, t.injection_member)}


@dataclass(frozen=True)
class Hit:
    path: Path
    what: str
    sample: str


@cache
def _people() -> dict[str, str]:
    values: dict[str, str] = {}
    for tenant in all_tenants():
        for number, m in generate(tenant).items():
            values[number] = "member number"
            for label, value in (
                ("name", m.display_name),
                ("phone", m.phone),
                ("email", m.email),
                ("street", m.street),
            ):
                values[value] = label
    return values


def _secrets() -> list[str]:
    env = dotenv_values(REPO / ".env") if (REPO / ".env").is_file() else {}
    return [v for k, v in env.items() if v and k.endswith("PASSWORD")]


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():  # pyright: ignore[reportUnknownVariableType]
            yield str(k)  # pyright: ignore[reportUnknownArgumentType]
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:  # pyright: ignore[reportUnknownVariableType]
            yield from _strings(v)


def _texts(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".json":
        return list(_strings(json.loads(raw)))
    if path.suffix == ".jsonl":
        return [s for line in raw.splitlines() if line.strip() for s in _strings(json.loads(line))]
    return [raw]


def scan(root: Path) -> list[Hit]:
    patterns = load_policy(REPO / "policies" / "default-teller.yaml").sensitivity_patterns
    compiled = [
        (p.name, re.compile(p.regex, re.IGNORECASE if p.ignore_case else 0), p.luhn)
        for p in patterns
    ]
    people = _people()
    secrets = _secrets()
    hits: list[Hit] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in TEXT:
            continue
        exempt = path.name == "README.md" or path.parent.name == "artifact"
        allowed = README_ALLOWED if exempt else set()
        for text in _texts(path):
            for name, rx, luhn in compiled:
                for m in rx.finditer(text):
                    value = m.group("v") if "v" in rx.groupindex else m.group(0)
                    if luhn and not luhn_ok(value):
                        continue
                    hits.append(Hit(path, name, value))
            for value, what in people.items():
                if value in allowed or value not in text:
                    continue
                # A member number stands alone: not inside a longer number or a hash.
                bounded = rf"(?<![0-9A-Za-z]){value}(?![0-9A-Za-z])"
                if what == "member number" and not re.search(bounded, text):
                    continue
                hits.append(Hit(path, what, value))
            for secret in secrets:
                if secret in text:
                    hits.append(Hit(path, "password", "(a .env password)"))
            if re.search(r"sk-ant-[A-Za-z0-9_-]{8,}", text):
                hits.append(Hit(path, "api key", "sk-ant-…"))
    return hits

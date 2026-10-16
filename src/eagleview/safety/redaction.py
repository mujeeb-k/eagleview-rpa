"""Three-layer redaction, applied by evidence.writer to everything it writes.

1. Typed: fields a capability declares `identifier`, `pii` or `secret` are masked (`****0482`,
   initials for names) wherever they appear under an `inputs` or `outputs` key.
2. Value-aware: the run registers its input values, extracted outputs and resolved secrets, and
   those exact strings (plus their HTML- and URL-encoded forms) are scrubbed from every string,
   case-insensitively and regardless of what surrounds them.
3. Pattern: the policy's sensitivity regexes (SSN, card numbers with a Luhn check, email, phone,
   labelled DOB and account numbers) are applied to every string.

Redaction walks the object tree before anything is serialized, so JSON escaping cannot hide a
value from it. Numbers (int, float, Decimal) are changed only where a field declares them
sensitive; anywhere else they are written as they are, so counters and timings survive and results
reload. Amounts in a sensitive field are masked unless the policy sets `redact_amounts: false`,
which only the synthetic training policy does."""

import html
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import PurePath
from typing import cast
from urllib.parse import quote, quote_plus

from pydantic import BaseModel

from eagleview.schema.capability import Capability, OutputSpec
from eagleview.schema.common import Sensitivity
from eagleview.schema.policy import Policy, SensitivityPattern

MIN_VALUE_LENGTH = 3  # shorter values would scrub half the screen
MIN_NAME_TOKEN_LENGTH = 3  # shorter tokens ("Li", "Q") would hit ordinary words
_FIELD_NAME = re.compile(r"[a-z][a-z0-9_]*")
_SCRUBBED = (Sensitivity.identifier, Sensitivity.pii, Sensitivity.secret)
_PLACEHOLDER = re.compile(r"(\[[a-z0-9_.]+\])")


def luhn_ok(text: str) -> bool:
    digits = [int(c) for c in text if c.isdigit()]
    if len(digits) < 12:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def mask_identifier(value: str) -> str:
    return "****" + value[-4:] if len(value) > 4 else "****"


def mask_name(value: str) -> str:
    initials = [w[0].upper() + "." for w in re.findall(r"[^\W\d_]+", value)]
    return " ".join(initials) if initials else "[name]"


def is_name_field(name: str) -> bool:
    return name == "name" or name.endswith("_name")


def mask_typed(
    value: object, sensitivity: Sensitivity, field_name: str, *, redact_amounts: bool = True
) -> object:
    """Layer 1 for one value of a declared field. Flags and nulls pass through."""
    if value is None or isinstance(value, bool) or sensitivity == Sensitivity.none:
        return value
    if sensitivity == Sensitivity.secret:
        return "[secret]"
    if isinstance(value, int | float | Decimal):
        if sensitivity == Sensitivity.identifier:
            return mask_identifier(str(value))
        return "[amount]" if redact_amounts else value
    if not isinstance(value, str):
        return value
    if sensitivity == Sensitivity.identifier:
        return mask_identifier(value)
    return mask_name(value) if is_name_field(field_name) else "[pii]"


def amount_variants(value: Decimal | int | float) -> set[str]:
    """How an amount is written in text: `1234.56`, `1,234.56`, `$1,234.56`."""
    number = Decimal(str(value))
    plain = format(abs(number), "f")
    grouped = format(abs(number), ",f")
    return {plain, grouped, "$" + plain, "$" + grouped}


def _class_boundary(token: str) -> str:
    """Match `token` only where it is not glued to more of the same kind of character, so the
    token `ANN` scrubs `Ann Lee` but leaves `ANNUAL` alone."""
    before = r"(?<![^\W\d_])" if token[0].isalpha() else r"(?<!\d)" if token[0].isdigit() else ""
    after = r"(?![^\W\d_])" if token[-1].isalpha() else r"(?!\d)" if token[-1].isdigit() else ""
    return before + re.escape(token) + after


@dataclass
class _Compiled:
    values: re.Pattern[str] | None
    tokens: re.Pattern[str] | None
    labels: dict[str, str]


@dataclass
class Redactor:
    patterns: Sequence[SensitivityPattern] = ()
    # Production-safe default: amounts in sensitive fields are masked. Only a synthetic training
    # policy turns this off.
    redact_amounts: bool = True
    input_sensitivity: dict[str, Sensitivity] = field(default_factory=dict[str, Sensitivity])
    output_specs: dict[str, OutputSpec] = field(default_factory=dict[str, OutputSpec])
    _values: dict[str, str] = field(default_factory=dict[str, str])  # casefolded value -> label
    _tokens: dict[str, str] = field(default_factory=dict[str, str])  # casefolded token -> label
    _compiled: _Compiled | None = None

    def __post_init__(self) -> None:
        self._pattern_regexes = [
            (p, re.compile(p.regex, re.IGNORECASE if p.ignore_case else 0)) for p in self.patterns
        ]

    @classmethod
    def from_policy(cls, policy: Policy) -> "Redactor":
        return cls(policy.sensitivity_patterns, redact_amounts=policy.redact_amounts)

    # --- registration -------------------------------------------------------------------------

    def use_capability(self, capability: Capability) -> None:
        """Adopt a capability's declared sensitivities for typed masking."""
        self.input_sensitivity = {k: v.sensitivity for k, v in capability.inputs.items()}
        self.output_specs = dict(capability.outputs)

    def register(
        self, value: object, sensitivity: Sensitivity, label: str, *, is_name: bool = False
    ) -> None:
        """Scrub `value` from everything written from now on (layer 2)."""
        if sensitivity not in _SCRUBBED or value is None or isinstance(value, bool):
            return
        if isinstance(value, Decimal | int | float) and sensitivity == Sensitivity.pii:
            if self.redact_amounts:
                # Matched with digit boundaries, so 0.00 does not hit $10,000.00.
                for variant in amount_variants(value):
                    if len(variant) >= MIN_VALUE_LENGTH:
                        self._tokens[variant.casefold()] = label
                self._compiled = None
            return
        text = " ".join(str(value).split())
        minimum = 1 if sensitivity == Sensitivity.secret else MIN_VALUE_LENGTH
        for variant in {
            text,
            str(value),
            html.escape(text),
            quote(text, safe=""),
            quote_plus(text),
        }:
            if len(variant) >= minimum:
                self._values[variant.casefold()] = label
        if is_name:
            for token in re.findall(r"[^\W\d_]+(?:['-][^\W\d_]+)*", text):
                if len(token) >= MIN_NAME_TOKEN_LENGTH:
                    self._tokens[token.casefold()] = label
        self._compiled = None

    def register_secret(self, value: str) -> None:
        self.register(value, Sensitivity.secret, "secret")

    def register_inputs(self, values: Mapping[str, object]) -> None:
        for name, value in values.items():
            sensitivity = self.input_sensitivity.get(name, Sensitivity.none)
            self.register(value, sensitivity, name, is_name=is_name_field(name))

    def register_outputs(self, values: Mapping[str, object]) -> None:
        for name, value in values.items():
            spec = self.output_specs.get(name)
            if spec is None:
                continue
            if spec.items is not None and isinstance(value, list):
                for row in cast(list[object], value):
                    if not isinstance(row, Mapping):
                        continue
                    for key, cell in cast(Mapping[str, object], row).items():
                        item = spec.items.get(key)
                        sensitivity = (item.sensitivity if item else None) or spec.sensitivity
                        self.register(
                            cell, sensitivity, f"{name}.{key}", is_name=is_name_field(key)
                        )
            else:
                self.register(value, spec.sensitivity, name, is_name=is_name_field(name))

    # --- layers 2 and 3 on text ---------------------------------------------------------------

    def _compile(self) -> _Compiled:
        if self._compiled is None:
            labels = {**self._tokens, **self._values}
            values = [re.escape(v) for v in sorted(self._values, key=len, reverse=True)]
            tokens = [_class_boundary(t) for t in sorted(self._tokens, key=len, reverse=True)]
            self._compiled = _Compiled(
                re.compile("|".join(values), re.IGNORECASE) if values else None,
                re.compile("|".join(tokens), re.IGNORECASE) if tokens else None,
                labels,
            )
        return self._compiled

    def scrub_values(self, text: str) -> str:
        """Whole values first, anywhere; then name tokens as words. Two passes, because removing
        a whole value can leave a glued-on remainder standing alone as a name token."""
        compiled = self._compile()
        labels = compiled.labels

        def label(m: re.Match[str]) -> str:
            return f"[{labels.get(m.group(0).casefold(), 'redacted')}]"

        if compiled.values is not None:
            text = compiled.values.sub(label, text)
        if compiled.tokens is not None:
            # Leave the placeholders from pass 1 alone: a name token "name" must not rewrite
            # "[member_name]" into "[member_[member_name]]".
            tokens = compiled.tokens
            text = "".join(
                part if _PLACEHOLDER.fullmatch(part) else tokens.sub(label, part)
                for part in _PLACEHOLDER.split(text)
            )
        return text

    def scrub_patterns(self, text: str) -> str:
        for pattern, regex in self._pattern_regexes:

            def replace(m: re.Match[str], p: SensitivityPattern = pattern) -> str:
                if p.luhn and not luhn_ok(m.group(0)):
                    return m.group(0)
                if "v" in m.re.groupindex and m.group("v") is not None:
                    start, end = m.span("v")
                    offset = m.start()
                    whole = m.group(0)
                    return whole[: start - offset] + f"[{p.name}]" + whole[end - offset :]
                return f"[{p.name}]"

            text = regex.sub(replace, text)
        return text

    def redact_text(self, text: str) -> str:
        return self.scrub_patterns(self.scrub_values(text))

    def is_sensitive(self, text: str) -> bool:
        return self.redact_text(text) != text

    # --- layer 1 plus everything, over a tree -------------------------------------------------

    def _typed(self, key: str, value: object) -> object:
        if key == "inputs" and isinstance(value, Mapping):
            mapping = cast(Mapping[str, object], value)
            return {
                k: mask_typed(
                    v,
                    self.input_sensitivity.get(k, Sensitivity.none),
                    k,
                    redact_amounts=self.redact_amounts,
                )
                for k, v in mapping.items()
            }
        if key == "outputs" and isinstance(value, Mapping):
            mapping = cast(Mapping[str, object], value)
            return {k: self._mask_output(k, v) for k, v in mapping.items()}
        return value

    def _mask_output(self, name: str, value: object) -> object:
        spec = self.output_specs.get(name)
        if spec is None:
            return value
        if spec.items is not None and isinstance(value, list):
            rows: list[object] = []
            for row in cast(list[object], value):
                if isinstance(row, Mapping):
                    masked: dict[str, object] = {}
                    for key, cell in cast(Mapping[str, object], row).items():
                        item = spec.items.get(key)
                        sensitivity = (item.sensitivity if item else None) or spec.sensitivity
                        masked[key] = mask_typed(
                            cell, sensitivity, key, redact_amounts=self.redact_amounts
                        )
                    rows.append(masked)
                else:
                    rows.append(row)
            return rows
        return mask_typed(value, spec.sensitivity, name, redact_amounts=self.redact_amounts)

    def redact_tree(self, value: object, *, exempt_keys: Iterable[str] = ()) -> object:
        """A JSON-ready copy of `value` with all three layers applied. Top-level keys listed in
        `exempt_keys` are copied as-is (fields the writer generated itself)."""
        exempt = frozenset(exempt_keys)
        if isinstance(value, BaseModel):
            value = value.model_dump(by_alias=True, exclude_none=True)
        if isinstance(value, Mapping) and exempt:
            mapping = cast(Mapping[object, object], value)
            out: dict[str, object] = {}
            for k, v in mapping.items():
                key = str(k)
                if key in exempt:
                    out[key] = v
                else:
                    out[self._key(key)] = self._walk(self._typed(key, v))
            return out
        return self._walk(value)

    def _key(self, key: str) -> str:
        """A mapping key that is a field name (snake_case, as step ids, input, output and timing
        names are) is not redacted: scrubbing a value that happens to appear inside one corrupts
        it (`submit_member_search` once became `submit_member_[acct_holder_no]`, because
        "Search" was on screen beside a sensitive label). Two exceptions keep personal data out:
        a field-name-shaped key carrying a registered number (`member_10482`), or containing a
        word of a registered person's name (a member named "Member" makes `member_id` reveal
        it), is scrubbed. Any other key (spaces, punctuation, a name) is scrubbed like a value."""
        if _FIELD_NAME.fullmatch(key):
            numbers = re.findall(r"\d{4,}", key)
            words = re.findall(r"[a-z]+", key)
            carries_data = any(n in self._values or n in self._tokens for n in numbers) or any(
                w in self._tokens for w in words
            )
            if not carries_data:
                return key
        return self.redact_text(key)

    def _walk(self, value: object) -> object:
        if isinstance(value, BaseModel):
            # Python mode keeps Decimals as Decimals, so typed masking can tell an amount from a
            # string; _walk serializes them afterwards.
            value = value.model_dump(by_alias=True, exclude_none=True)
        if isinstance(value, str):
            return self.redact_text(value)
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, int | float | Decimal):
            # Numbers outside declared fields are never scrubbed: a timing of 10482 ms is not a
            # member number, and rewriting it would break the result's types on reload.
            return str(value) if isinstance(value, Decimal) else value
        if isinstance(value, Mapping):
            mapping = cast(Mapping[object, object], value)
            return {
                self._key(str(k)): self._walk(self._typed(str(k), v)) for k, v in mapping.items()
            }
        if isinstance(value, list | tuple | set | frozenset):
            return [self._walk(v) for v in cast(Iterable[object], value)]
        if isinstance(value, datetime | date):
            return value.isoformat()
        if isinstance(value, PurePath):
            return self.redact_text(str(value))
        return self.redact_text(repr(value))

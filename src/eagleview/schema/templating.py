"""The two tiny languages an artifact may contain.

Templates are restricted to `{{ inputs.x }}` (the app profile's login macro may also use
`{{ credentials.username }}` and `{{ credentials.password }}`). `when` is a boolean expression over
inputs: `==`, `!=`, `and`, `or`, `not`, parentheses, string/integer/boolean literals, and a bare
`inputs.x` for truthiness. There is nothing else: no calls, no attribute access, no arithmetic."""

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import cast

_TEMPLATE_REF = re.compile(r"\{\{\s*([a-z]+)\.([a-z][a-z0-9_]*)\s*\}\}")


class TemplateError(ValueError):
    pass


def template_refs(
    text: str, roots: frozenset[str] = frozenset({"inputs"})
) -> list[tuple[str, str]]:
    """Return every (root, name) referenced in `text`. Anything else between braces is an error."""
    refs: list[tuple[str, str]] = []
    for match in _TEMPLATE_REF.finditer(text):
        root, name = match.group(1), match.group(2)
        if root not in roots:
            raise TemplateError(f"template root {root!r} is not allowed here: {match.group(0)!r}")
        refs.append((root, name))
    leftover = _TEMPLATE_REF.sub("", text)
    if "{{" in leftover or "}}" in leftover:
        raise TemplateError(f"only {{{{ inputs.<name> }}}} templates are allowed: {text!r}")
    return refs


def render_template(text: str, values: Mapping[str, Mapping[str, object]]) -> str:
    """Substitute `{{ root.name }}` from `values[root][name]`. Missing names are an error."""

    def sub(match: re.Match[str]) -> str:
        root, name = match.group(1), match.group(2)
        try:
            value = values[root][name]
        except KeyError as exc:
            raise TemplateError(f"no value for {root}.{name}") from exc
        return str(value)

    template_refs(text, frozenset(values))
    return _TEMPLATE_REF.sub(sub, text)


def iter_strings(value: object) -> Iterator[str]:
    """Every string inside a JSON-like tree, keys excluded."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in cast(Mapping[object, object], value).values():
            yield from iter_strings(item)
    elif isinstance(value, list | tuple):
        for item in cast(list[object] | tuple[object, ...], value):
            yield from iter_strings(item)


# --- `when` expressions ---------------------------------------------------------------------------

Literal_ = str | int | bool


@dataclass(frozen=True)
class InputRef:
    name: str


@dataclass(frozen=True)
class Const:
    value: Literal_


@dataclass(frozen=True)
class Compare:
    op: str  # "==" or "!="
    left: "Expr"
    right: "Expr"


@dataclass(frozen=True)
class BoolOp:
    op: str  # "and" or "or"
    left: "Expr"
    right: "Expr"


@dataclass(frozen=True)
class Negate:
    operand: "Expr"


Expr = InputRef | Const | Compare | BoolOp | Negate

_TOKEN = re.compile(
    r"""\s*(?:
        (?P<str>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
      | (?P<int>-?\d+)
      | (?P<op>==|!=|\(|\))
      | (?P<ref>inputs\.[a-z][a-z0-9_]*)
      | (?P<word>[A-Za-z_][A-Za-z0-9_.]*)
    )""",
    re.VERBOSE,
)


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    text = text.rstrip()
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None or match.end() == pos:
            raise TemplateError(f"unexpected character at {pos} in when: {text!r}")
        kind = match.lastgroup
        assert kind is not None
        tokens.append((kind, match.group(kind)))
        pos = match.end()
    return tokens


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.tokens = _tokenize(text)
        self.pos = 0

    def _peek(self) -> tuple[str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _take(self) -> tuple[str, str]:
        token = self._peek()
        if token is None:
            raise TemplateError(f"unexpected end of when: {self.text!r}")
        self.pos += 1
        return token

    def _is_word(self, word: str) -> bool:
        token = self._peek()
        return token is not None and token == ("word", word)

    def parse(self) -> Expr:
        expr = self._or()
        if self._peek() is not None:
            raise TemplateError(f"unexpected {self._peek()!r} in when: {self.text!r}")
        return expr

    def _or(self) -> Expr:
        left = self._and()
        while self._is_word("or"):
            self._take()
            left = BoolOp("or", left, self._and())
        return left

    def _and(self) -> Expr:
        left = self._not()
        while self._is_word("and"):
            self._take()
            left = BoolOp("and", left, self._not())
        return left

    def _not(self) -> Expr:
        if self._is_word("not"):
            self._take()
            return Negate(self._not())
        return self._compare()

    def _compare(self) -> Expr:
        left = self._atom()
        token = self._peek()
        if token is not None and token[0] == "op" and token[1] in ("==", "!="):
            self._take()
            return Compare(token[1], left, self._atom())
        return left

    def _atom(self) -> Expr:
        kind, value = self._take()
        if kind == "op" and value == "(":
            expr = self._or()
            if self._take() != ("op", ")"):
                raise TemplateError(f"missing ')' in when: {self.text!r}")
            return expr
        if kind == "ref":
            return InputRef(value.removeprefix("inputs."))
        if kind == "str":
            body = value[1:-1]
            return Const(re.sub(r"\\(.)", r"\1", body))
        if kind == "int":
            return Const(int(value))
        if kind == "word" and value in ("true", "false"):
            return Const(value == "true")
        raise TemplateError(f"unexpected {value!r} in when: {self.text!r}")


def parse_when(text: str) -> Expr:
    return _Parser(text).parse()


def when_refs(expr: Expr) -> set[str]:
    match expr:
        case InputRef(name=name):
            return {name}
        case Const():
            return set()
        case Compare(left=left, right=right) | BoolOp(left=left, right=right):
            return when_refs(left) | when_refs(right)
        case Negate(operand=operand):
            return when_refs(operand)


def _value(expr: Expr, inputs: Mapping[str, object]) -> object:
    match expr:
        case InputRef(name=name):
            return inputs.get(name)
        case Const(value=value):
            return value
        case Compare(op=op, left=left, right=right):
            equal = _normalize(_value(left, inputs)) == _normalize(_value(right, inputs))
            return equal if op == "==" else not equal
        case BoolOp(op=op, left=left, right=right):
            if op == "and":
                return bool(_value(left, inputs)) and bool(_value(right, inputs))
            return bool(_value(left, inputs)) or bool(_value(right, inputs))
        case Negate(operand=operand):
            return not _value(operand, inputs)


def _normalize(value: object) -> object:
    # Inputs arrive typed (int, Decimal, bool, str); literals are str, int or bool.
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return int(value)
    return value


def evaluate_when(text: str, inputs: Mapping[str, object]) -> bool:
    return bool(_value(parse_when(text), inputs))

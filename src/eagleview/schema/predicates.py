"""State predicates. On disk each predicate is a single-key mapping, `{text_present: {...}}`, and
they combine with `all`, `any` and `not`. All but `url_matches` are surface-agnostic."""

from collections.abc import Iterator
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag

from eagleview.schema.common import FramePath, Regex, Slug
from eagleview.schema.locators import TargetSpec

_STRICT = ConfigDict(extra="forbid")


class TextArgs(BaseModel):
    model_config = _STRICT
    frame: FramePath = Field(default_factory=list[str])  # empty: any frame
    text: str = Field(min_length=1)


class PatternInFrameArgs(BaseModel):
    model_config = _STRICT
    frame: FramePath = Field(default_factory=list[str])
    pattern: Regex


class PatternArgs(BaseModel):
    model_config = _STRICT
    pattern: Regex


class ElementArgs(BaseModel):
    model_config = _STRICT
    target: TargetSpec


class FieldValueArgs(BaseModel):
    model_config = _STRICT
    # "self": the target of the step this predicate belongs to.
    target: Literal["self"] | TargetSpec
    equals: str


class TextPresent(BaseModel):
    model_config = _STRICT
    text_present: TextArgs


class TextMatches(BaseModel):
    model_config = _STRICT
    text_matches: PatternInFrameArgs


class ElementPresent(BaseModel):
    model_config = _STRICT
    element_present: ElementArgs


class FieldValue(BaseModel):
    model_config = _STRICT
    field_value: FieldValueArgs


class UrlMatches(BaseModel):
    """Web surfaces only: desktop apps have no URL."""

    model_config = _STRICT
    url_matches: PatternArgs


class TitleMatches(BaseModel):
    model_config = _STRICT
    title_matches: PatternArgs


class OutputPresent(BaseModel):
    model_config = _STRICT
    output_present: Slug


class AllOf(BaseModel):
    model_config = _STRICT
    all: list["Predicate"] = Field(min_length=1)


class AnyOf(BaseModel):
    model_config = _STRICT
    any: list["Predicate"] = Field(min_length=1)


class Not(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)
    not_: "Predicate" = Field(alias="not")


_KIND_BY_TYPE: dict[type[BaseModel], str] = {
    TextPresent: "text_present",
    TextMatches: "text_matches",
    ElementPresent: "element_present",
    FieldValue: "field_value",
    UrlMatches: "url_matches",
    TitleMatches: "title_matches",
    OutputPresent: "output_present",
    AllOf: "all",
    AnyOf: "any",
    Not: "not",
}
_KINDS = frozenset(_KIND_BY_TYPE.values())


def _predicate_kind(value: Any) -> str | None:
    if isinstance(value, BaseModel):
        return _KIND_BY_TYPE.get(type(value))
    if isinstance(value, dict):
        keys = cast(dict[object, object], value).keys()
        if len(keys) == 1:
            (key,) = keys
            if isinstance(key, str) and key in _KINDS:
                return key
    return None


Predicate = Annotated[
    Annotated[TextPresent, Tag("text_present")]
    | Annotated[TextMatches, Tag("text_matches")]
    | Annotated[ElementPresent, Tag("element_present")]
    | Annotated[FieldValue, Tag("field_value")]
    | Annotated[UrlMatches, Tag("url_matches")]
    | Annotated[TitleMatches, Tag("title_matches")]
    | Annotated[OutputPresent, Tag("output_present")]
    | Annotated[AllOf, Tag("all")]
    | Annotated[AnyOf, Tag("any")]
    | Annotated[Not, Tag("not")],
    Discriminator(
        _predicate_kind,
        custom_error_type="predicate",
        custom_error_message="a predicate is a mapping with exactly one known key",
    ),
]

AnyPredicate = (
    TextPresent
    | TextMatches
    | ElementPresent
    | FieldValue
    | UrlMatches
    | TitleMatches
    | OutputPresent
    | AllOf
    | AnyOf
    | Not
)


def predicate_kind(pred: AnyPredicate) -> str:
    return _KIND_BY_TYPE[type(pred)]


def walk_predicate(pred: AnyPredicate) -> Iterator[AnyPredicate]:
    """Yield `pred` and every predicate nested inside it, depth first."""
    yield pred
    match pred:
        case AllOf(all=children) | AnyOf(any=children):
            for child in children:
                yield from walk_predicate(child)
        case Not(not_=child):
            yield from walk_predicate(child)
        case _:
            pass


for _model in (AllOf, AnyOf, Not):
    _model.model_rebuild()

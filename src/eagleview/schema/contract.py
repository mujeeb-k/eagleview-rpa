"""Validate values against a capability's typed inputs and outputs.

Inputs are checked in preflight, before the UI is touched (`rejected: INPUT_INVALID`). Outputs are
checked after extraction (`failed: OUTPUT_INVALID`). Decimals stay Decimal; floats are refused."""

import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import cast

from eagleview.schema.capability import FieldSpec, InputSpec, OutputSpec, ScalarType
from eagleview.schema.result import FieldError, OutputValue, Scalar

_DECIMAL = re.compile(r"^-?\d+(\.\d+)?$")


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int) and not isinstance(value, bool):
        return Decimal(value)
    if isinstance(value, str) and _DECIMAL.fullmatch(value.strip()):
        try:
            return Decimal(value.strip())
        except InvalidOperation:
            return None
    return None


def _scalar(
    field: str, value: object, kind: ScalarType, *, scale: int | None, pattern: str | None
) -> tuple[Scalar, list[FieldError]]:
    if kind == "string":
        if not isinstance(value, str):
            return None, [FieldError(field=field, code="type", message="expected a string")]
        if pattern is not None and re.fullmatch(pattern, value) is None:
            return None, [
                FieldError(field=field, code="pattern", message=f"does not match {pattern}")
            ]
        return value, []
    if kind == "integer":
        if isinstance(value, bool):
            return None, [FieldError(field=field, code="type", message="expected an integer")]
        if isinstance(value, int):
            return value, []
        if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
            return int(value.strip()), []
        return None, [FieldError(field=field, code="type", message="expected an integer")]
    if kind == "boolean":
        if isinstance(value, bool):
            return value, []
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true", []
        return None, [FieldError(field=field, code="type", message="expected true or false")]
    number = _decimal(value)
    if number is None:
        return None, [
            FieldError(field=field, code="type", message="expected a decimal, never a float")
        ]
    if scale is not None:
        exponent = number.as_tuple().exponent
        if isinstance(exponent, int) and -exponent > scale:
            return None, [
                FieldError(field=field, code="scale", message=f"more than {scale} decimal places")
            ]
        number = number.quantize(Decimal(1).scaleb(-scale))
    return number, []


def validate_inputs(
    specs: Mapping[str, InputSpec], raw: Mapping[str, object]
) -> tuple[dict[str, Scalar], list[FieldError]]:
    values: dict[str, Scalar] = {}
    errors: list[FieldError] = []
    for name in raw:
        if name not in specs:
            errors.append(FieldError(field=name, code="unknown", message="not a declared input"))
    for name, spec in specs.items():
        if name not in raw:
            if spec.required:
                errors.append(FieldError(field=name, code="missing", message="required input"))
            continue
        value, problems = _scalar(name, raw[name], spec.type, scale=None, pattern=spec.pattern)
        if not problems and isinstance(value, str):
            if spec.enum is not None and value not in spec.enum:
                problems.append(FieldError(field=name, code="enum", message=f"one of {spec.enum}"))
            if spec.min_length is not None and len(value) < spec.min_length:
                problems.append(FieldError(field=name, code="length", message="too short"))
            if spec.max_length is not None and len(value) > spec.max_length:
                problems.append(FieldError(field=name, code="length", message="too long"))
        errors.extend(problems)
        if not problems:
            values[name] = value
    return values, errors


def _item(
    field: str, raw: object, items: Mapping[str, FieldSpec]
) -> tuple[dict[str, Scalar], list[FieldError]]:
    if not isinstance(raw, Mapping):
        return {}, [FieldError(field=field, code="type", message="expected an object")]
    row = cast(Mapping[str, object], raw)
    out: dict[str, Scalar] = {}
    errors: list[FieldError] = []
    for key in row:
        if key not in items:
            errors.append(
                FieldError(field=f"{field}.{key}", code="unknown", message="not a declared field")
            )
    for key, spec in items.items():
        if key not in row:
            errors.append(
                FieldError(field=f"{field}.{key}", code="missing", message="missing field")
            )
            continue
        value, problems = _scalar(
            f"{field}.{key}", row[key], spec.type, scale=spec.scale, pattern=spec.pattern
        )
        errors.extend(problems)
        out[key] = value
    return out, errors


def validate_outputs(
    specs: Mapping[str, OutputSpec], raw: Mapping[str, object]
) -> tuple[dict[str, OutputValue], list[FieldError]]:
    values: dict[str, OutputValue] = {}
    errors: list[FieldError] = []
    for name in raw:
        if name not in specs:
            errors.append(FieldError(field=name, code="unknown", message="not a declared output"))
    for name, spec in specs.items():
        if name not in raw or raw[name] is None:
            if spec.required:
                errors.append(FieldError(field=name, code="missing", message="required output"))
            continue
        value = raw[name]
        if spec.type == "array":
            assert spec.items is not None
            if not isinstance(value, list):
                errors.append(FieldError(field=name, code="type", message="expected a list"))
                continue
            rows: list[dict[str, Scalar]] = []
            for i, item in enumerate(cast(list[object], value)):
                row, problems = _item(f"{name}[{i}]", item, spec.items)
                errors.extend(problems)
                rows.append(row)
            values[name] = rows
        else:
            scalar, problems = _scalar(
                name, value, spec.type, scale=spec.scale, pattern=spec.pattern
            )
            errors.extend(problems)
            values[name] = scalar
    return values, errors

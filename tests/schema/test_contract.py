"""Input and output validation against the capability contract."""

from decimal import Decimal

from eagleview.artifacts.yaml_io import load_capability
from eagleview.schema.contract import validate_inputs, validate_outputs
from tests.fixtures import CAPABILITY

CAP = load_capability(CAPABILITY)


def codes(errors: list) -> list[tuple[str, str]]:  # type: ignore[type-arg]
    return [(e.field, e.code) for e in errors]


def test_inputs() -> None:
    values, errors = validate_inputs(CAP.inputs, {"member_id": "10482"})
    assert values == {"member_id": "10482"} and not errors
    # Both tenants' formats fit the shared pattern.
    assert not validate_inputs(CAP.inputs, {"member_id": "2210236"})[1]
    assert codes(validate_inputs(CAP.inputs, {"member_id": "10A82"})[1]) == [
        ("member_id", "pattern")
    ]
    assert codes(validate_inputs(CAP.inputs, {})[1]) == [("member_id", "missing")]
    assert codes(validate_inputs(CAP.inputs, {"member_id": 10482})[1]) == [("member_id", "type")]
    assert ("ssn", "unknown") in codes(
        validate_inputs(CAP.inputs, {"member_id": "10482", "ssn": "1"})[1]
    )


def test_outputs_parse_decimals_and_reject_floats() -> None:
    raw = {
        "member_name": "DOE, JANE Q",
        "share_accounts": [
            {
                "suffix": "S00",
                "description": "Regular Share",
                "balance": "1234.5",
                "available": Decimal("1200"),
            },
        ],
    }
    values, errors = validate_outputs(CAP.outputs, raw)
    assert not errors
    row = values["share_accounts"][0]  # type: ignore[index]
    assert row["balance"] == Decimal("1234.50") and str(row["available"]) == "1200.00"  # type: ignore[index]

    raw["share_accounts"][0]["balance"] = 1234.5  # type: ignore[index]
    assert ("share_accounts[0].balance", "type") in codes(validate_outputs(CAP.outputs, raw)[1])
    raw["share_accounts"][0]["balance"] = "1.005"  # type: ignore[index]
    assert ("share_accounts[0].balance", "scale") in codes(validate_outputs(CAP.outputs, raw)[1])
    raw["share_accounts"][0]["balance"] = "1.00"  # type: ignore[index]
    raw["share_accounts"][0]["suffix"] = "s-00"  # type: ignore[index]
    assert ("share_accounts[0].suffix", "pattern") in codes(validate_outputs(CAP.outputs, raw)[1])
    assert ("member_name", "missing") in codes(
        validate_outputs(CAP.outputs, {"share_accounts": []})[1]
    )
    assert ("share_accounts", "type") in codes(
        validate_outputs(CAP.outputs, {"member_name": "X", "share_accounts": "none"})[1]
    )

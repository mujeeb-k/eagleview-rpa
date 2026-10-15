"""Redaction: each layer on its own, and the over-redaction limits that keep evidence useful."""

from decimal import Decimal

import pytest

from eagleview.artifacts.yaml_io import load_capability, load_policy
from eagleview.safety.redaction import Redactor, luhn_ok, mask_identifier, mask_name, mask_typed
from eagleview.safety.secrets import SecretMissing, SecretResolver, env_var
from eagleview.schema.common import Sensitivity
from tests.fixtures import CAPABILITY, POLICY


@pytest.fixture
def redactor() -> Redactor:
    r = Redactor(load_policy(POLICY).sensitivity_patterns)
    r.use_capability(load_capability(CAPABILITY))
    return r


# --- layer 1: typed -------------------------------------------------------------------------------


def test_typed_masks() -> None:
    assert mask_identifier("10482") == "****0482"
    assert mask_identifier("0482") == "****"
    assert mask_name("DOE, JANE Q") == "D. J. Q."
    assert mask_typed("Regular Share", Sensitivity.pii, "description") == "[pii]"
    assert mask_typed("hunter2", Sensitivity.secret, "password") == "[secret]"
    assert mask_typed(Decimal("12.50"), Sensitivity.pii, "balance") == Decimal("12.50")
    assert mask_typed("10482", Sensitivity.none, "x") == "10482"


def test_typed_masking_follows_the_capability(redactor: Redactor) -> None:
    out = redactor.redact_tree(
        {
            "inputs": {"member_id": "10482"},
            "outputs": {
                "member_name": "DOE, JANE Q",
                "share_accounts": [
                    {"suffix": "S00", "description": "Regular Share", "balance": Decimal("1234.56")}
                ],
            },
        }
    )
    assert out == {
        "inputs": {"member_id": "****0482"},
        "outputs": {
            "member_name": "D. J. Q.",
            # Item fields inherit the array's sensitivity; amounts pass through as strings.
            "share_accounts": [{"suffix": "[pii]", "description": "[pii]", "balance": "1234.56"}],
        },
    }


# --- layer 2: value-aware -------------------------------------------------------------------------


def test_registered_values_are_scrubbed_everywhere(redactor: Redactor) -> None:
    redactor.register_inputs({"member_id": "10482"})
    redactor.register_outputs({"member_name": "DOE, JANE Q"})
    text = (
        "member10482 opened; id=10482&x; Member: doe, jane q; JANE DOE called; "
        "DOE%2C+JANE+Q in a URL; DOE, JANE Q in html"
    )
    out = redactor.redact_text(text)
    for leaked in ("10482", "DOE", "JANE", "doe", "jane"):
        assert leaked not in out, out
    assert "[member_id]" in out and "[member_name]" in out


def test_name_tokens_respect_word_class_and_minimum_length(redactor: Redactor) -> None:
    redactor.register("Ann Q Li", Sensitivity.pii, "member_name", is_name=True)
    out = redactor.redact_text("Annual fee for ANN; li-ion; Q branch; Ann Q Li")
    assert "Annual" in out  # "Ann" is not scrubbed inside a longer word
    assert "li-ion" in out and "Q branch" in out  # tokens under 3 characters are ignored
    assert "ANN;" not in out
    assert out.endswith("[member_name]")  # the full value is still scrubbed


def test_short_and_empty_values_are_ignored_but_secrets_are_not(redactor: Redactor) -> None:
    redactor.register("", Sensitivity.identifier, "x")
    redactor.register("ab", Sensitivity.identifier, "x")
    assert redactor.redact_text("abcab") == "abcab"
    redactor.register_secret("pw")
    assert redactor.redact_text("pw=pw") == "[secret]=[secret]"


def test_amounts_are_never_registered(redactor: Redactor) -> None:
    redactor.register_outputs(
        {
            "share_accounts": [
                {"suffix": "S00", "description": "Regular Share", "balance": Decimal("1234.56")}
            ]
        }
    )
    out = redactor.redact_text("Regular Share S00 $1,234.56 1234.56")
    assert out == "[share_accounts.description] [share_accounts.suffix] $1,234.56 1234.56"


def test_secret_resolver_registers_values() -> None:
    redactor = Redactor()
    resolver = SecretResolver(
        {"LANTERN_BAY_TELLER_PASSWORD": "Tr4ining!pw"}, redactor.register_secret
    )
    assert env_var("secret://lantern_bay_teller_password") == "LANTERN_BAY_TELLER_PASSWORD"
    assert resolver.resolve("secret://lantern_bay_teller_password") == "Tr4ining!pw"
    assert redactor.redact_text("typed Tr4ining!pw into the box") == "typed [secret] into the box"
    with pytest.raises(SecretMissing) as info:
        resolver.resolve("secret://copperhill_teller_password")
    assert "COPPERHILL_TELLER_PASSWORD" in str(info.value)
    with pytest.raises(ValueError):
        resolver.resolve("lantern_bay_teller_password")


# --- layer 3: patterns, and what they must leave alone --------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("SSN 123-45-6789 on file", "SSN [ssn] on file"),
        ("card 4111 1111 1111 1111.", "card [card]."),
        ("card 4111-1111-1111-1111", "card [card]"),
        ("not a card 4111 1111 1111 1112", "not a card 4111 1111 1111 1112"),  # fails Luhn
        ("mail jane.doe42@example.com now", "mail [email] now"),
        ("call (555) 555-0123 or 555.555.0199", "call [phone] or [phone]"),
        ("DOB: 04/12/1961", "DOB: [dob]"),
        ("date of birth 4/2/1961", "date of birth [dob]"),
        ("Birthdate 12/31/1999", "Birthdate [dob]"),
        ("Member # 10482", "Member # [account_number]"),
        ("Acct Holder No. 2210236", "Acct Holder No. [account_number]"),
        ("account number: 1234-5678", "account number: [account_number]"),
    ],
)
def test_patterns_scrub(redactor: Redactor, text: str, expected: str) -> None:
    assert redactor.redact_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # Posting and transaction dates are not DOBs: only a DOB/birth label makes a date sensitive.
        "10/14/2026 S01 POS DEBIT GROCERY 45.00-",
        "Member Since: 03/02/2011",
        "Next Due 11/01/2026",
        "posted 2026-10-15",
        # Business numbers that are not account numbers.
        "Transaction 7200001 posted",
        "Drawer 04 over its cash limit ($10,000.00).",
        "Balance $1,234.56 Available $1,200.00",
        "Ironbridge Teller v7.2.3 - TRAINING REGION - synthetic data",
        # Masked on screen already: not a full SSN.
        "SSN: ***-**-6789 DOB: **/**/1961",
    ],
)
def test_patterns_leave_ordinary_text_alone(redactor: Redactor, text: str) -> None:
    assert redactor.redact_text(text) == text


def test_luhn() -> None:
    assert luhn_ok("4111111111111111")
    assert not luhn_ok("4111111111111112")
    assert not luhn_ok("12345")

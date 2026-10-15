"""Fault spec parsing and one-shot semantics (no server)."""

import pytest

from mock_bank.faults import Faults, FaultSpecError


def test_parse_every_fault() -> None:
    f = Faults()
    specs = {
        "session_expire_after_requests": "3",
        "system_notice_on": "/member",
        "unknown_modal_on": "/inq",
        "slow_ms": "/inq:1500",
        "error500_on": "/member:once",
        "role": "inquiry_only",
        "maintenance": "on",
    }
    f.apply(specs)
    assert f.describe() == specs


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("session_expire_after_requests", "-1"),
        ("session_expire_after_requests", "x"),
        ("system_notice_on", "member"),
        ("slow_ms", "/inq"),
        ("slow_ms", "/inq:fast"),
        ("error500_on", "/inq:twice"),
        ("role", "admin"),
        ("maintenance", "yes"),
        ("explode", "1"),
    ],
)
def test_bad_spec_rejected(key: str, value: str) -> None:
    with pytest.raises(FaultSpecError):
        Faults().apply({key: value})


def test_bad_spec_changes_nothing() -> None:
    f = Faults()
    with pytest.raises(FaultSpecError):
        f.apply({"system_notice_on": "/member", "role": "admin"})
    assert f.describe() == {}


def test_session_expiry_counts_then_disarms() -> None:
    f = Faults()
    f.apply({"session_expire_after_requests": "2"})
    assert [f.take_expiry() for _ in range(5)] == [False, False, True, False, False]
    assert f.describe() == {}


def test_error500_once_and_always() -> None:
    once = Faults()
    once.apply({"error500_on": "/member:once"})
    assert [once.take_error500("/inq"), once.take_error500("/member")] == [False, True]
    assert once.take_error500("/member") is False
    always = Faults()
    always.apply({"error500_on": "/member"})
    assert all(always.take_error500("/member") for _ in range(3))

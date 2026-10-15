"""App profile, overlay, policy, tenant, result and intervention models; schema versions; money;
templating; version ranges; and the committed JSON Schemas."""

import copy
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from eagleview.artifacts.json_schema import render_schemas
from eagleview.artifacts.yaml_io import (
    load_app_profile,
    load_capability,
    load_overlay,
    load_policy,
    load_yaml,
)
from eagleview.schema.app_profile import AppProfile, Dismiss, SelectThenClick
from eagleview.schema.common import (
    Money,
    UnsupportedSchemaVersion,
    version_in_range,
)
from eagleview.schema.intervention import Intervention
from eagleview.schema.locators import strategy_applies
from eagleview.schema.overlay import Overlay
from eagleview.schema.result import FailureCategory, RunResult
from eagleview.schema.templating import TemplateError, evaluate_when, render_template
from eagleview.schema.tenant import TenantProfile
from tests.fixtures import APP_PROFILE, CAPABILITY, OVERLAY, POLICY, REPO

# --- schema_version -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "loader"),
    [(CAPABILITY, load_capability), (APP_PROFILE, load_app_profile), (OVERLAY, load_overlay)],
)
@pytest.mark.parametrize("version", ["2.0", "0.9", "10.1"])
def test_unsupported_schema_major_fails_clearly(
    tmp_path: Path, source: Path, loader: Any, version: str
) -> None:
    text = source.read_text()
    if "schema_version:" in text:
        text = text.replace('schema_version: "1.0"', f'schema_version: "{version}"')
    else:
        text = f'schema_version: "{version}"\n' + text
    path = tmp_path / source.name
    path.write_text(text)
    with pytest.raises(UnsupportedSchemaVersion) as info:
        loader(path)
    message = str(info.value)
    assert str(path) in message
    assert f"unsupported schema_version '{version}'" in message
    assert "1.x" in message


def test_schema_version_minor_is_accepted_and_shape_is_checked() -> None:
    data = load_yaml(APP_PROFILE.read_text())
    data["schema_version"] = "1.3"
    assert AppProfile.model_validate(data).schema_version == "1.3"
    data["schema_version"] = 1.0  # unquoted in YAML
    with pytest.raises(ValidationError, match="quoted 'MAJOR.MINOR'"):
        AppProfile.model_validate(data)
    data["schema_version"] = "2.0"
    with pytest.raises(ValidationError, match="unsupported schema_version '2.0'"):
        AppProfile.model_validate(data)


# --- app profile ----------------------------------------------------------------------------------


def test_app_profile_fixture() -> None:
    profile = load_app_profile(APP_PROFILE)
    assert [s.id for s in profile.global_states] == [
        "session_expired",
        "app_error",
        "system_notice",
        "permission_denied",
        "maintenance_banner",
    ]
    assert isinstance(profile.global_states[2].recover, Dismiss)
    assert profile.global_states[3].outcome is not None
    assert profile.global_states[4].fail is not None and profile.global_states[4].fail.retryable
    assert profile.surface == "legacy_web"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["login"]["steps"][0].update(value="{{ inputs.member_id }}"), "root 'inputs'"),
        (lambda d: d["login"]["steps"][0].update(value="{{ credentials.pin }}"), "not 'pin'"),
        (lambda d: d["login"]["steps"][0].pop("value"), "only with, fill"),
        (lambda d: d["global_states"][3].update(fail={"category": "APP_ERROR"}), "cannot also"),
        (lambda d: d["global_states"][4].pop("fail"), "needs recover, fail or outcome"),
        (lambda d: d["global_states"][4].update(max_per_run=1), "only applies to recover"),
        (lambda d: d["global_states"].append(d["global_states"][0]), "duplicate global state"),
        (lambda d: d["fingerprint"]["version"].update(pattern=r"v(\d+)"), "named group"),
        (lambda d: d["global_states"][1]["fail"].update(category="OOPS"), "Input should be"),
        (lambda d: d.update(versions=">=7.2;<8"), "not a version comparator"),
    ],
)
def test_app_profile_rules(mutate: Any, message: str) -> None:
    data = load_yaml(APP_PROFILE.read_text())
    mutate(data)
    with pytest.raises(ValidationError, match=message):
        AppProfile.model_validate(data)


# --- overlay --------------------------------------------------------------------------------------


def test_overlay_fixture() -> None:
    overlay = load_overlay(OVERLAY)
    assert overlay.tenant == "copperhill"
    assert overlay.label_aliases["Member #"] == ["Acct Holder No."]
    recover = overlay.global_states_add[0].recover
    assert isinstance(recover, SelectThenClick) and recover.select.option_index == 0
    patch = overlay.step_patches["submit_search"]
    assert patch.target is not None and patch.target.locators is not None
    assert patch.target.locators.prepend[0].strategy == "text"


def test_overlay_scope_and_patch_rules() -> None:
    data = load_yaml(OVERLAY.read_text())
    version_patch = copy.deepcopy(data)
    del version_patch["tenant"]
    with pytest.raises(ValidationError, match="names a tenant, or a version range"):
        Overlay.model_validate(version_patch)
    version_patch["applies_to"]["versions"] = ">=7.4,<7.5"
    assert Overlay.model_validate(version_patch).tenant is None
    data["step_patches"]["submit_search"]["target"]["locators"]["replace"] = []
    with pytest.raises(ValidationError, match="replace cannot be combined"):
        Overlay.model_validate(data)


# --- policy, tenant, intervention -----------------------------------------------------------------


def test_committed_policy_loads() -> None:
    policy = load_policy(POLICY)
    assert policy.allowed_origins["lantern-bay"] == ["http://127.0.0.1:8601"]
    assert {p.name for p in policy.sensitivity_patterns} >= {"ssn", "card", "email", "phone", "dob"}


def test_policy_rejects_origin_with_path_and_overlapping_action_lists() -> None:
    data = load_yaml(POLICY.read_text())
    bad = copy.deepcopy(data)
    bad["allowed_origins"]["lantern-bay"] = ["http://127.0.0.1:8601/teller"]
    with pytest.raises(ValidationError, match="not a scheme://host"):
        load_policy_data(bad)
    bad = copy.deepcopy(data)
    bad["action_types"]["deny"].append("click")
    with pytest.raises(ValidationError, match="both allowed and denied"):
        load_policy_data(bad)


def load_policy_data(data: dict[str, Any]) -> object:
    from eagleview.schema.policy import Policy

    return Policy.model_validate(data)


def test_tenant_password_must_be_a_secret_reference() -> None:
    base = {
        "slug": "lantern-bay",
        "name": "Lantern Bay Federal Credit Union",
        "app_profile": "ironbridge-teller",
        "base_url": "http://127.0.0.1:8601",
        "credentials": {"username": "teller01", "password": "secret://lantern_bay_teller_password"},
    }
    TenantProfile.model_validate(base)
    with pytest.raises(ValidationError):
        TenantProfile.model_validate(
            {**base, "credentials": {"username": "t", "password": "hunter2"}}
        )
    with pytest.raises(ValidationError):
        TenantProfile.model_validate({**base, "base_url": "http://user@127.0.0.1:8601"})


def test_intervention_requires_consistent_resolution() -> None:
    base: dict[str, Any] = {
        "id": "iv_1",
        "kind": "takeover",
        "run_id": "r_1",
        "capability": "member.lookup_share_balances@1.0.0",
        "step_id": "submit_search",
        "reason": "UNKNOWN_STATE",
        "state_summary": "modal 'Cash Drawer Alert'",
        "allowed_actions": ["take_control", "abort"],
        "created_at": datetime(2026, 10, 15, tzinfo=UTC),
    }
    Intervention.model_validate(base)
    with pytest.raises(ValidationError, match="go together"):
        Intervention.model_validate({**base, "resolution": "resumed"})
    with pytest.raises(ValidationError, match="capability .replay. or a goal"):
        Intervention.model_validate({**base, "goal": "look up member"})


# --- result ---------------------------------------------------------------------------------------


def _result(**kw: Any) -> RunResult:
    return RunResult.model_validate(
        {
            "run_id": "r_abc",
            "capability": "member.lookup_share_balances@1.0.0",
            "tenant": "lantern-bay",
            **kw,
        }
    )


def test_run_result_status_matches_payload() -> None:
    ok = _result(
        status="success",
        outputs={"member_name": "DOE, JANE Q", "share_accounts": [{"balance": Decimal("1234.50")}]},
    )
    dumped = json.loads(ok.model_dump_json())
    assert dumped["outputs"]["share_accounts"][0]["balance"] == "1234.50"  # money as a string
    _result(
        status="outcome",
        outcome={"code": "MEMBER_NOT_FOUND", "message": "x", "at_step": "submit_search"},
    )
    _result(status="rejected", rejection={"code": "INPUT_INVALID", "errors": []})
    _result(status="failed", failure={"category": "APP_ERROR", "retryable": False})
    _result(status="needs_human")
    with pytest.raises(ValidationError, match="needs its success data"):
        _result(status="success")
    with pytest.raises(ValidationError, match="cannot carry failed data"):
        _result(
            status="outcome",
            outcome={"code": "X", "message": "x"},
            failure={"category": "APP_ERROR"},
        )
    with pytest.raises(ValidationError):
        _result(status="failed", failure={"category": "SOMETHING_ELSE"})
    assert len(FailureCategory) == 14


# --- money ----------------------------------------------------------------------------------------


def test_money_is_decimal_never_float() -> None:
    money = TypeAdapter(Money)
    assert money.validate_python("1234.56") == Decimal("1234.56")
    assert money.validate_python(Decimal("0.10")) == Decimal("0.10")
    assert money.dump_python(Decimal("0.10"), mode="json") == "0.10"
    for bad in (0.1, True, "1,234.56", "NaN", "1e3", Decimal("Infinity")):
        with pytest.raises(ValidationError):
            money.validate_python(bad)
    assert money.json_schema() == {"type": "string", "pattern": r"^-?\d+(\.\d+)?$"}


# --- templating and when --------------------------------------------------------------------------


def test_render_template_and_when() -> None:
    values = {"inputs": {"member_id": "10482", "kind": "club", "n": 2}}
    assert render_template("id={{ inputs.member_id }}", values) == "id=10482"
    with pytest.raises(TemplateError):
        render_template("{{ inputs.missing }}", values)
    inputs = values["inputs"]
    assert evaluate_when("inputs.kind == 'club'", inputs)
    assert evaluate_when('not inputs.kind != "club" and (inputs.n == 2 or false)', inputs)
    assert not evaluate_when("inputs.absent", inputs)
    assert evaluate_when("inputs.n == 2", {"n": Decimal("2")})


# --- versions and strategies ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "expected"),
    [("7.2", True), ("7.2.3", True), ("7.4.0", True), ("7.1.9", False), ("8.0", False)],
)
def test_version_range(version: str, expected: bool) -> None:
    assert version_in_range(version, ">=7.2,<8.0") is expected


def test_strategy_surfaces() -> None:
    assert strategy_applies("css", "legacy_web")
    assert not strategy_applies("css", "desktop")
    assert not strategy_applies("xpath", "desktop")
    assert strategy_applies("anchor", "desktop")
    assert strategy_applies("a11y", "desktop")


# --- JSON Schema ----------------------------------------------------------------------------------


def test_committed_json_schemas_are_current() -> None:
    """`schemas/` is generated. If this fails, run `uv run eagleview schema export`."""
    for name, text in render_schemas().items():
        committed = REPO / "schemas" / name
        assert committed.exists(), f"missing {committed}"
        assert committed.read_text() == text, f"{committed} is stale"

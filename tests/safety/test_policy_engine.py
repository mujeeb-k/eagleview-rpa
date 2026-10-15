"""Policy engine table tests: action x target x URL x actor x mode, against the committed policy."""

from typing import Any

import pytest

from eagleview.artifacts.yaml_io import load_policy
from eagleview.safety.policy_engine import (
    ApprovalToken,
    PolicyContext,
    PolicyEngine,
    TargetInfo,
    normalize_key,
    normalize_path,
)
from eagleview.schema.action import Action
from tests.fixtures import POLICY

LB = "http://127.0.0.1:8601"
CH = "http://127.0.0.1:8602"


@pytest.fixture(scope="module")
def engine() -> PolicyEngine:
    return PolicyEngine(load_policy(POLICY))


def ctx(**kw: Any) -> PolicyContext:
    base: dict[str, Any] = {
        "tenant": "lantern-bay",
        "mode": "replay",
        "run_id": "r_1",
        "step_id": "post_share",
        "current_url": f"{LB}/share/review",
    }
    return PolicyContext(**{**base, **kw})


APPROVED = frozenset({ApprovalToken("r_1", "post_share")})

# (action, target label, context overrides, actor, expected verdict, expected reason)
CHECK_TABLE: list[tuple[dict[str, Any], str | None, dict[str, Any], str, str, str]] = [
    # Ordinary, read-only interaction.
    ({"type": "click", "x": 5, "y": 5}, "Member Inquiry", {}, "automation", "allow", "ok"),
    ({"type": "fill", "text": "10482"}, "Member #", {}, "llm", "allow", "ok"),
    ({"type": "click", "x": 5, "y": 5}, "Validate", {}, "automation", "allow", "ok"),
    # Observation is always fine, even on a page off the allowlist.
    ({"type": "screenshot"}, None, {"current_url": "https://evil.test/"}, "llm", "allow", "ok"),
    ({"type": "zoom", "region": (0, 0, 10, 10)}, None, {}, "llm", "allow", "ok"),
    # Denied and unlisted action types.
    ({"type": "drag", "x": 1, "y": 1}, None, {}, "llm", "block", "action_denied"),
    ({"type": "upload"}, None, {}, "operator:dana", "block", "action_denied"),
    ({"type": "download"}, None, {}, "automation", "block", "action_denied"),
    ({"type": "right_click", "x": 1, "y": 1}, None, {}, "llm", "block", "action_denied"),
    # Keys: a small allowlist, chords normalized.
    ({"type": "key", "key": "Tab"}, None, {}, "llm", "allow", "ok"),
    ({"type": "key", "key": "Shift+Tab"}, None, {}, "llm", "allow", "ok"),
    ({"type": "key", "key": "Control+A"}, None, {}, "llm", "allow", "ok"),
    ({"type": "key", "key": "ctrl+l"}, None, {}, "llm", "block", "key_not_allowed"),
    ({"type": "key", "key": "alt+F4"}, None, {}, "llm", "block", "key_not_allowed"),
    ({"type": "key", "key": "F12"}, None, {}, "llm", "block", "key_not_allowed"),
    ({"type": "key"}, None, {}, "llm", "block", "key_not_allowed"),
    # Navigation: own origin only, admin routes never.
    ({"type": "navigate", "url": f"{LB}/inq"}, None, {}, "llm", "allow", "ok"),
    ({"type": "navigate", "url": f"{CH}/inq"}, None, {}, "llm", "block", "origin_not_allowed"),
    (
        {"type": "navigate", "url": "http://localhost:8601/inq"},
        None,
        {},
        "llm",
        "block",
        "origin_not_allowed",
    ),
    (
        {"type": "navigate", "url": "https://127.0.0.1:8601/"},
        None,
        {},
        "llm",
        "block",
        "origin_not_allowed",
    ),
    ({"type": "navigate", "url": f"{LB}@evil.test/"}, None, {}, "llm", "block", "userinfo_in_url"),
    ({"type": "navigate", "url": f"{LB}/__admin/faults"}, None, {}, "llm", "block", "route_denied"),
    ({"type": "navigate", "url": f"{LB}/__admin"}, None, {}, "llm", "block", "route_denied"),
    ({"type": "navigate", "url": f"{LB}/__admin/"}, None, {}, "llm", "block", "route_denied"),
    ({"type": "navigate", "url": f"{LB}/__ADMIN/faults"}, None, {}, "llm", "block", "route_denied"),
    (
        {"type": "navigate", "url": f"{LB}/%5F%5Fadmin/faults"},
        None,
        {},
        "llm",
        "block",
        "route_denied",
    ),
    (
        {"type": "navigate", "url": f"{LB}/%255F%255Fadmin/faults"},
        None,
        {},
        "llm",
        "block",
        "route_denied",
    ),
    (
        {"type": "navigate", "url": f"{LB}/inq/../__admin/faults"},
        None,
        {},
        "llm",
        "block",
        "route_denied",
    ),
    (
        {"type": "navigate", "url": f"{LB}//__admin//faults?x=1"},
        None,
        {},
        "llm",
        "block",
        "route_denied",
    ),
    ({"type": "navigate", "url": f"{LB}/__administrator"}, None, {}, "llm", "allow", "ok"),
    (
        {"type": "navigate", "url": "javascript:alert(1)"},
        None,
        {},
        "llm",
        "block",
        "origin_not_allowed",
    ),
    ({"type": "navigate", "url": f"{LB}:99999/"}, None, {}, "llm", "block", "bad_url"),
    (
        {"type": "navigate", "url": f"{LB}/__admin/faults"},
        None,
        {},
        "operator:dana",
        "block",
        "route_denied",
    ),
    (
        {"type": "navigate", "url": "https://evil.test/"},
        None,
        {},
        "operator:dana",
        "block",
        "origin_not_allowed",
    ),
    # Acting on a page that is off the allowlist is refused.
    (
        {"type": "click", "x": 1, "y": 1},
        "OK",
        {"current_url": "https://evil.test/x"},
        "llm",
        "block",
        "page_not_allowed",
    ),
    (
        {"type": "click", "x": 1, "y": 1},
        "OK",
        {"current_url": f"{LB}/__admin/faults"},
        "llm",
        "block",
        "page_not_allowed",
    ),
    (
        {"type": "click", "x": 1, "y": 1},
        "OK",
        {"current_url": f"{CH}/inq"},
        "llm",
        "block",
        "page_not_allowed",
    ),
    ({"type": "click", "x": 1, "y": 1}, "OK", {"current_url": "about:blank"}, "llm", "allow", "ok"),
    # Irreversible labels: whole words only.
    (
        {"type": "click", "x": 1, "y": 1},
        "Post",
        {},
        "automation",
        "require_approval",
        "irreversible_needs_approval",
    ),
    (
        {"type": "click", "x": 1, "y": 1},
        "  POST  ",
        {},
        "automation",
        "require_approval",
        "irreversible_needs_approval",
    ),
    (
        {"type": "double_click", "x": 1, "y": 1},
        "Approve transfer",
        {},
        "automation",
        "require_approval",
        "irreversible_needs_approval",
    ),
    ({"type": "click", "x": 1, "y": 1}, "Posted", {}, "automation", "allow", "ok"),
    ({"type": "click", "x": 1, "y": 1}, "Postal Code", {}, "automation", "allow", "ok"),
    ({"type": "click", "x": 1, "y": 1}, "Closed Accounts", {}, "automation", "allow", "ok"),
    ({"type": "click", "x": 1, "y": 1}, "Repost", {}, "automation", "allow", "ok"),
    ({"type": "fill", "text": "x"}, "Post Date", {}, "automation", "allow", "ok"),
    (
        {"type": "key", "key": "Return"},
        "Post",
        {},
        "automation",
        "require_approval",
        "irreversible_needs_approval",
    ),
    ({"type": "key", "key": "Tab"}, "Post", {}, "automation", "allow", "ok"),
    # Irreversible handling by mode and actor.
    (
        {"type": "click", "x": 1, "y": 1},
        "Post",
        {"mode": "discovery"},
        "llm",
        "escalate",
        "irreversible_in_discovery",
    ),
    (
        {"type": "click", "x": 1, "y": 1},
        "Post",
        {"approvals": APPROVED},
        "automation",
        "allow",
        "approved",
    ),
    (
        {"type": "click", "x": 1, "y": 1},
        "Post",
        {"approvals": APPROVED, "step_id": "other"},
        "automation",
        "require_approval",
        "irreversible_needs_approval",
    ),
    (
        {"type": "click", "x": 1, "y": 1},
        "Post",
        {"approvals": APPROVED, "run_id": "r_2"},
        "automation",
        "require_approval",
        "irreversible_needs_approval",
    ),
    (
        {"type": "click", "x": 1, "y": 1},
        "Post",
        {"step_id": None, "approvals": APPROVED},
        "automation",
        "require_approval",
        "irreversible_needs_approval",
    ),
    (
        {"type": "click", "x": 1, "y": 1},
        "Post",
        {},
        "operator:dana",
        "allow",
        "operator_irreversible",
    ),
    (
        {"type": "click", "x": 1, "y": 1},
        "Post",
        {"mode": "discovery"},
        "operator:dana",
        "allow",
        "operator_irreversible",
    ),
    # Unknown tenant.
    (
        {"type": "click", "x": 1, "y": 1},
        "OK",
        {"tenant": "nowhere"},
        "llm",
        "block",
        "unknown_tenant",
    ),
]


@pytest.mark.parametrize(
    ("action", "label", "overrides", "actor", "verdict", "reason"), CHECK_TABLE
)
def test_check_table(
    engine: PolicyEngine,
    action: dict[str, Any],
    label: str | None,
    overrides: dict[str, Any],
    actor: str,
    verdict: str,
    reason: str,
) -> None:
    decision = engine.check(Action(**action), actor, ctx(**overrides), TargetInfo(label=label))
    assert (decision.verdict, decision.reason) == (verdict, reason), decision.detail


def test_operator_irreversible_is_flagged_for_audit(engine: PolicyEngine) -> None:
    decision = engine.check(
        Action(type="click", x=1, y=1), "operator:dana", ctx(), TargetInfo(label="Post")
    )
    assert decision.allowed and decision.irreversible and decision.audit
    automated = engine.check(
        Action(type="click", x=1, y=1),
        "automation",
        ctx(approvals=APPROVED),
        TargetInfo(label="Post"),
    )
    assert automated.allowed and automated.irreversible and not automated.audit


# (method, url, context overrides, actor, expected verdict, expected reason)
REQUEST_TABLE: list[tuple[str, str, dict[str, Any], str, str, str]] = [
    ("GET", f"{LB}/member?m=1", {}, "automation", "allow", "ok"),
    ("POST", f"{LB}/inq", {}, "llm", "allow", "ok"),
    ("GET", f"{LB}/share/post", {}, "automation", "allow", "ok"),  # rule is POST only
    ("POST", f"{LB}/share/post", {}, "automation", "block", "irreversible_without_approval"),
    ("post", f"{LB}/share/post/", {}, "automation", "block", "irreversible_without_approval"),
    ("POST", f"{LB}/share/%70ost", {}, "automation", "block", "irreversible_without_approval"),
    ("POST", f"{LB}/share/post", {"approvals": APPROVED}, "automation", "allow", "approved"),
    (
        "POST",
        f"{LB}/share/post",
        {"approvals": APPROVED, "step_id": "x"},
        "automation",
        "block",
        "irreversible_without_approval",
    ),
    (
        "POST",
        f"{LB}/share/post",
        {"approvals": APPROVED, "mode": "discovery"},
        "llm",
        "block",
        "irreversible_without_approval",
    ),
    ("POST", f"{LB}/share/post", {}, "operator:dana", "allow", "operator_irreversible"),
    ("GET", f"{LB}/__admin/faults", {}, "harness", "block", "route_denied"),
    ("POST", f"{LB}/__admin/reset", {}, "operator:dana", "block", "route_denied"),
    ("GET", f"{CH}/inq", {}, "automation", "block", "origin_not_allowed"),
    ("GET", f"{CH}/inq", {"tenant": "copperhill"}, "automation", "allow", "ok"),
    ("GET", f"{LB}/inq", {"tenant": "copperhill"}, "automation", "block", "origin_not_allowed"),
    ("GET", "https://fonts.example.test/x.woff", {}, "automation", "block", "origin_not_allowed"),
    ("GET", "data:image/png;base64,AAAA", {}, "automation", "allow", "ok"),
    ("GET", "http://127.0.0.1:8601@evil.test/", {}, "automation", "block", "userinfo_in_url"),
    ("GET", "http://127.0.0.1:8601", {}, "automation", "allow", "ok"),
    ("GET", "http://127.0.0.1:80/", {}, "automation", "block", "origin_not_allowed"),
]


@pytest.mark.parametrize(
    ("method", "url", "overrides", "actor", "verdict", "reason"), REQUEST_TABLE
)
def test_request_table(
    engine: PolicyEngine,
    method: str,
    url: str,
    overrides: dict[str, Any],
    actor: str,
    verdict: str,
    reason: str,
) -> None:
    decision = engine.check_request(method, url, actor, ctx(**overrides))
    assert (decision.verdict, decision.reason) == (verdict, reason), decision.detail


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("/%5F%5Fadmin", "/__admin"),
        ("/%255F%255Fadmin", "/__admin"),
        ("//a///b/", "/a/b"),
        ("/a/./b/../c", "/a/c"),
        ("/../../etc", "/etc"),
        ("", "/"),
        ("\\__admin", "/__admin"),
    ],
)
def test_normalize_path(raw: str, normalized: str) -> None:
    assert normalize_path(raw) == normalized


def test_normalize_key() -> None:
    assert normalize_key("Shift+Tab") == normalize_key("shift+tab") == "shift+tab"
    assert normalize_key("Control+Shift+A") == normalize_key("shift+ctrl+a") == "ctrl+shift+a"
    assert normalize_key("Page_Up") == "pageup"
    assert normalize_key("cmd+v") == "meta+v"

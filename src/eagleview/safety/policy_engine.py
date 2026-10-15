"""The policy engine: one pure decision function per enforcement layer.

`check()` is the pre-action layer, called by `Surface.act()` before every UI action from any actor.
`check_request()` is the network-guard layer, called for every browser request. They are
independent on purpose: a mis-grounded click or a prompt-injected detour that the first layer
misses still has to get its request past the second."""

import posixpath
import re
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import SplitResult, unquote, urlsplit

from eagleview.schema.action import OBSERVE_ACTIONS, Action, is_operator
from eagleview.schema.policy import Policy, RouteRule

Mode = Literal["discovery", "replay"]
Verdict = Literal["allow", "block", "require_approval", "escalate"]

# Schemes that never leave the browser process.
_LOCAL_SCHEMES = frozenset({"about", "data", "blob"})
_ACTIVATING_KEYS = frozenset({"return", "enter", "space", "kpenter"})
_DEFAULT_PORTS = {"http": 80, "https": 443}
_MODIFIER_ALIASES = {
    "control": "ctrl",
    "cmd": "meta",
    "command": "meta",
    "super": "meta",
    "win": "meta",
}
_MODIFIERS = frozenset({"ctrl", "alt", "shift", "meta"})


@dataclass(frozen=True)
class ApprovalToken:
    """A human approval, bound to one step of one run. It authorizes nothing else."""

    run_id: str
    step_id: str


@dataclass(frozen=True)
class TargetInfo:
    """What grounding found under the pointer or focus."""

    label: str | None = None
    role: str | None = None


@dataclass(frozen=True)
class PolicyContext:
    tenant: str
    mode: Mode
    run_id: str
    step_id: str | None = None
    current_url: str | None = None
    approvals: frozenset[ApprovalToken] = field(default_factory=frozenset[ApprovalToken])

    def approved(self) -> bool:
        return (
            self.step_id is not None and ApprovalToken(self.run_id, self.step_id) in self.approvals
        )


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    reason: str  # a stable code, e.g. "origin_not_allowed"
    detail: str = ""
    irreversible: bool = False
    audit: bool = (
        False  # allowed, but must be called out in the log (an operator's irreversible act)
    )

    @property
    def allowed(self) -> bool:
        return self.verdict == "allow"


_ALLOW = Decision("allow", "ok")


def normalize_key(key: str) -> str:
    """`Shift+Tab` -> `shift+tab`, `Page_Up` -> `pageup`, `Control+A` -> `ctrl+a`."""
    parts = [p.strip().lower().replace("_", "") for p in key.split("+")]
    if any(not p for p in parts):
        return key.strip().lower()
    *mods, base = parts
    mods = sorted({_MODIFIER_ALIASES.get(m, m) for m in mods})
    return "+".join([*mods, base])


def normalize_path(path: str) -> str:
    """Decode percent-escapes until stable (the server decodes before routing, so `%5F%5Fadmin`
    is `__admin`), then collapse slashes and resolve dot segments."""
    previous = None
    while previous != path:
        previous, path = path, unquote(path)
    path = re.sub(r"/+", "/", "/" + path.replace("\\", "/"))
    path = posixpath.normpath(path)
    return "/" if path in ("", ".") else path


def _glob_regex(pattern: str) -> re.Pattern[str]:
    body = pattern.rstrip("/")
    tail = ""
    if body.endswith("/**"):
        body, tail = body[: -len("/**")], "(?:/.*)?"
    out = ""
    i = 0
    while i < len(body):
        if body.startswith("**", i):
            out += ".*"
            i += 2
        elif body[i] == "*":
            out += "[^/]*"
            i += 1
        else:
            out += re.escape(body[i])
            i += 1
    return re.compile(f"^{out}{tail}/?$", re.IGNORECASE)


def _split(url: str) -> SplitResult | None:
    try:
        parts = urlsplit(url.strip())
        _ = parts.port  # raises on a malformed port
    except ValueError:
        return None
    return parts


def _origin(parts: SplitResult) -> str | None:
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not parts.hostname:
        return None
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    port = parts.port or _DEFAULT_PORTS[scheme]
    return f"{scheme}://{host}:{port}"


class PolicyEngine:
    def __init__(self, policy: Policy) -> None:
        self.policy = policy
        self._origins: dict[str, frozenset[str]] = {}
        for tenant, origins in policy.allowed_origins.items():
            normalized = {_origin(p) for o in origins if (p := _split(o)) is not None}
            self._origins[tenant] = frozenset(o for o in normalized if o is not None)
        self._routes: list[tuple[RouteRule, re.Pattern[str]]] = [
            (rule, _glob_regex(rule.pattern)) for rule in policy.routes
        ]
        self._keys = frozenset(normalize_key(k) for k in policy.allowed_keys)
        words = "|".join(re.escape(w) for w in policy.irreversible_label_patterns)
        self._irreversible_label = re.compile(rf"\b(?:{words})\b", re.IGNORECASE) if words else None

    # --- building blocks ----------------------------------------------------------------------

    def is_irreversible_label(self, label: str | None) -> bool:
        if not label or self._irreversible_label is None:
            return False
        return self._irreversible_label.search(" ".join(label.split())) is not None

    def route_effect(self, method: str, path: str) -> RouteRule | None:
        """The first matching rule for this method and path, denies before irreversible."""
        normalized = normalize_path(path)
        matches = [
            rule
            for rule, regex in self._routes
            if regex.match(normalized) and ("*" in rule.methods or method.upper() in rule.methods)
        ]
        for effect in ("deny", "irreversible"):
            for rule in matches:
                if rule.effect == effect:
                    return rule
        return None

    def _check_url(self, tenant: str, url: str, method: str) -> Decision | RouteRule | None:
        """A block decision, the matching irreversible rule, or None when the URL is fine."""
        parts = _split(url)
        if parts is None:
            return Decision("block", "bad_url", f"cannot parse {url!r}")
        scheme = parts.scheme.lower()
        if scheme in _LOCAL_SCHEMES:
            return None
        if parts.username is not None or parts.password is not None or "@" in parts.netloc:
            return Decision("block", "userinfo_in_url", "URLs with credentials are never followed")
        origin = _origin(parts)
        if origin is None or origin not in self._origins.get(tenant, frozenset()):
            return Decision(
                "block", "origin_not_allowed", f"{origin or url!r} is not allowed for {tenant}"
            )
        rule = self.route_effect(method, parts.path)
        if rule is not None and rule.effect == "deny":
            return Decision(
                "block", "route_denied", f"{method.upper()} {parts.path} matches {rule.pattern}"
            )
        return rule

    def _irreversible(self, actor: str, ctx: PolicyContext, what: str) -> Decision:
        if is_operator(actor):
            return Decision("allow", "operator_irreversible", what, irreversible=True, audit=True)
        if ctx.mode == "discovery":
            if self.policy.irreversible_handling.discovery == "escalate":
                return Decision("escalate", "irreversible_in_discovery", what, irreversible=True)
            return Decision("block", "irreversible_in_discovery", what, irreversible=True)
        if ctx.approved():
            return Decision("allow", "approved", what, irreversible=True)
        if self.policy.irreversible_handling.replay == "require_approval":
            return Decision(
                "require_approval", "irreversible_needs_approval", what, irreversible=True
            )
        return Decision("block", "irreversible_blocked", what, irreversible=True)

    # --- layer 1: pre-action check ------------------------------------------------------------

    def check(
        self, action: Action, actor: str, ctx: PolicyContext, target: TargetInfo | None = None
    ) -> Decision:
        if ctx.tenant not in self._origins:
            return Decision("block", "unknown_tenant", f"no policy for tenant {ctx.tenant!r}")
        if action.type in self.policy.action_types.deny:
            return Decision("block", "action_denied", f"{action.type} is denied by policy")
        if action.type not in self.policy.action_types.allow:
            return Decision("block", "action_not_allowed", f"{action.type} is not on the allowlist")
        if action.type in OBSERVE_ACTIONS:
            return _ALLOW

        # Acting on a page that is itself off the allowlist is refused, whatever the action.
        if ctx.current_url is not None:
            found = self._check_url(ctx.tenant, ctx.current_url, "GET")
            if isinstance(found, Decision):
                return Decision("block", "page_not_allowed", f"current page: {found.detail}")

        if action.type == "key" and (
            action.key is None or normalize_key(action.key) not in self._keys
        ):
            return Decision(
                "block", "key_not_allowed", f"key {action.key!r} is not on the allowlist"
            )

        if action.type == "navigate":
            if action.url is None:
                return Decision("block", "bad_url", "navigate needs a url")
            found = self._check_url(ctx.tenant, action.url, "GET")
            if isinstance(found, Decision):
                return found
            if found is not None:
                return self._irreversible(actor, ctx, f"navigate to {found.pattern}")

        label = target.label if target is not None else None
        activates = action.type in ("click", "double_click") or (
            action.type == "key"
            and action.key is not None
            and normalize_key(action.key) in _ACTIVATING_KEYS
        )
        if activates and self.is_irreversible_label(label):
            return self._irreversible(actor, ctx, f"{action.type} on {label!r}")
        return _ALLOW

    # --- layer 2: network guard ---------------------------------------------------------------

    def check_request(self, method: str, url: str, actor: str, ctx: PolicyContext) -> Decision:
        if ctx.tenant not in self._origins:
            return Decision("block", "unknown_tenant", f"no policy for tenant {ctx.tenant!r}")
        found = self._check_url(ctx.tenant, url, method)
        if isinstance(found, Decision):
            return found
        if found is None:
            return _ALLOW
        # An irreversible route reached without an approval token means the pre-action layer was
        # bypassed (a mis-grounded click, a JS redirect). The guard cannot wait for a human, so it
        # blocks rather than escalates.
        if is_operator(actor):
            return Decision(
                "allow", "operator_irreversible", f"{method} {url}", irreversible=True, audit=True
            )
        if ctx.mode == "replay" and ctx.approved():
            return Decision("allow", "approved", f"{method} {url}", irreversible=True)
        return Decision(
            "block",
            "irreversible_without_approval",
            f"{method.upper()} {found.pattern}",
            irreversible=True,
        )

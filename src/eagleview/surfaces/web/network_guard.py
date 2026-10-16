"""The network guard: the second, independent enforcement layer. Every browser request passes
`PolicyEngine.check_request` before it leaves, whatever caused it: a mis-grounded click, a
prompt-injected detour, a script. A blocked request is aborted and logged.

Redirects need care. Playwright calls a route handler only for the first request in a redirect
chain, so an allowed URL that answers 302 to `/__admin` would get through unseen. The guard
therefore fetches every request itself with redirects off, checks each `Location` against the
policy, and:
- for a page or frame navigation, answers with a stub page that does `location.replace(target)`,
  so the next hop is a fresh browser request that comes back through this guard;
- for anything else (fetch, XHR, an image), follows the chain itself, hop by hop, checking each.

The policy context and actor are read when each request arrives, never captured at install."""

import contextlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin

from playwright.async_api import APIResponse, BrowserContext, Route
from playwright.async_api import Error as PlaywrightError

from eagleview.safety.policy_engine import ApprovalToken, Decision, PolicyContext, PolicyEngine
from eagleview.surfaces.base import BlockedRequest, EventSink

MAX_REDIRECTS = 10


@dataclass
class GuardState:
    """Who is acting and under which context. The surface updates it on every act()."""

    actor: str
    ctx: PolicyContext


@dataclass
class NetworkGuard:
    engine: PolicyEngine
    state: Callable[[], GuardState]
    sink: EventSink | None = None
    blocked: list[BlockedRequest] = field(default_factory=list[BlockedRequest])
    # Approvals already used. One token authorizes one irreversible request: a double-click, a
    # resubmit or a script cannot post twice on it. This matters more than it seems, because the
    # guard sends each request itself (route.fetch), so a browser cancelling a navigation no
    # longer stops the request it already handed over.
    spent: set[ApprovalToken] = field(default_factory=set[ApprovalToken])

    async def install(self, context: BrowserContext) -> None:
        await context.route("**/*", self._handle)

    def drain(self) -> list[BlockedRequest]:
        """Blocked requests since the last drain. The engine reads these while settling."""
        out, self.blocked = self.blocked, []
        return out

    def _check(self, method: str, url: str) -> tuple[Decision, GuardState]:
        st = self.state()
        decision = self.engine.check_request(method, url, st.actor, st.ctx)
        if decision.allowed and decision.reason == "approved" and st.ctx.step_id is not None:
            token = ApprovalToken(st.ctx.run_id, st.ctx.step_id)
            if token in self.spent:
                return Decision("block", "approval_spent", decision.detail, irreversible=True), st
            self.spent.add(token)
        return decision, st

    async def _block(
        self, route: Route, method: str, url: str, d: Decision, st: GuardState
    ) -> None:
        record = BlockedRequest(method, url, st.actor, st.ctx.step_id, d.reason, d.detail)
        self.blocked.append(record)
        if self.sink is not None:
            self.sink.event(
                "network.blocked",
                st.actor,
                method=method,
                url=url,
                reason=d.reason,
                detail=d.detail,
                step_id=st.ctx.step_id,
            )
        await route.abort("blockedbyclient")

    async def _handle(self, route: Route) -> None:
        req = route.request
        try:
            decision, st = self._check(req.method, req.url)
            if not decision.allowed:
                await self._block(route, req.method, req.url, decision, st)
                return
            response = await route.fetch(max_redirects=0)
            location = _location(response)
            if location is None:
                await route.fulfill(response=response)
                return
            target = urljoin(req.url, location)
            method = _method_after(req.method, response.status)
            decision, st = self._check(method, target)
            if not decision.allowed:
                await self._block(route, method, target, decision, st)
                return
            if req.resource_type == "document":
                await self._renavigate(route, method, target, st)
            else:
                await self._follow(route, method, target, req.post_data_buffer)
        except PlaywrightError:
            # The page or context went away mid-request, or the server dropped the connection.
            # Fail closed: the request is not completed.
            with contextlib.suppress(PlaywrightError):
                await route.abort("failed")

    async def _renavigate(self, route: Route, method: str, target: str, st: GuardState) -> None:
        if method != "GET":
            # A 307/308 that would replay a POST into another URL. A stub page can only GET, and
            # replaying a body silently is exactly what the guard exists to prevent.
            d = Decision("block", "method_preserving_redirect", f"{method} {target}")
            await self._block(route, method, target, d, st)
            return
        script = json.dumps(target).replace("</", "<\\/")
        body = f"<!doctype html><script>location.replace({script});</script>"
        await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)

    async def _follow(self, route: Route, method: str, url: str, body: bytes | None) -> None:
        for _ in range(MAX_REDIRECTS):
            response = await route.fetch(
                url=url, method=method, post_data=body if method != "GET" else None, max_redirects=0
            )
            location = _location(response)
            if location is None:
                await route.fulfill(response=response)
                return
            url = urljoin(url, location)
            method = _method_after(method, response.status)
            decision, st = self._check(method, url)
            if not decision.allowed:
                await self._block(route, method, url, decision, st)
                return
        await route.abort("failed")


def _location(response: APIResponse) -> str | None:
    if 300 <= response.status < 400:
        return response.headers.get("location") or None
    return None


def _method_after(method: str, status: int) -> str:
    """307 and 308 keep the method and body; the others turn a POST into a GET."""
    if status in (307, 308):
        return method.upper()
    return "HEAD" if method.upper() == "HEAD" else "GET"

"""The network guard: the second enforcement layer, independent of Surface.act. Every request,
whatever caused it, is checked against the policy before it leaves the browser.

Each test opens its own surface, so its own browser context and page: a navigation one test
leaves in flight (a blocked request commits Chrome's error page late) can never reach another.
At most one top-level navigation is checked per test for the same reason."""

import asyncio
import contextlib
from dataclasses import replace
from typing import Any

from playwright.async_api import Error as PlaywrightError

from eagleview.safety.policy_engine import ApprovalToken
from eagleview.surfaces.web.network_guard import BlockedRequest
from eagleview.surfaces.web.playwright_surface import PlaywrightSurface
from tests.support.bank import Bank
from tests.surfaces.conftest import RUN_ID, Events, Site
from tests.surfaces.helpers import act_on, button, pred
from tests.surfaces.test_act import _to_review


async def _blocked(surface: PlaywrightSurface, timeout_s: float = 5.0) -> list[BlockedRequest]:
    """Wait for the guard to record a block; requests leave the page asynchronously."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while not surface.guard.blocked:
        assert loop.time() < deadline, "nothing was blocked"
        await asyncio.sleep(0.05)
    return surface.guard.drain()


def _money_market_count(bank: Bank) -> int:
    description = bank.tenant.share_types["money_market"].description
    with bank.signed_on_client() as c:
        page = c.post("/inq", data={"txtA": bank.demo, "txtB": ""})
        return page.text.count(description)


async def test_a_script_cannot_reach_the_admin_api(
    open_surface: Any, lantern: Bank, events: Events
) -> None:
    """A prompt-injected detour or page script: no act() involved, the guard still blocks it."""
    surface: PlaywrightSurface = await open_surface(lantern)
    main = surface.page.frame(name="main")
    assert main is not None
    status = await main.evaluate(
        "() => fetch('/__admin/faults').then(r => 'reached ' + r.status, e => 'blocked')"
    )
    assert status == "blocked"
    blocked = surface.guard.drain()
    assert [(b.method, b.reason) for b in blocked] == [("GET", "route_denied")]
    assert events.of("network.blocked")[0]["reason"] == "route_denied"
    # Encoded paths normalize to the same route.
    encoded = "() => fetch('/%5F%5Fadmin/faults').then(() => 'reached', () => 'blocked')"
    assert await main.evaluate(encoded) == "blocked"


async def test_another_tenants_origin_is_off_the_allowlist(
    open_surface: Any, lantern: Bank, copperhill: Bank
) -> None:
    surface: PlaywrightSurface = await open_surface(lantern)
    main = surface.page.frame(name="main")
    assert main is not None
    result = await main.evaluate(
        f"() => fetch('{copperhill.url}/signon', {{mode: 'no-cors'}})"
        ".then(() => 'reached', () => 'blocked')"
    )
    assert result == "blocked"
    assert surface.guard.drain()[0].reason == "origin_not_allowed"


async def test_posting_without_approval_is_blocked_even_when_act_is_bypassed(
    open_surface: Any, lantern: Bank
) -> None:
    """Layer 1 is skipped entirely (a script submits the review form); layer 2 still stops it."""
    surface: PlaywrightSurface = await open_surface(lantern)
    await _to_review(surface, lantern)
    main = surface.page.frame(name="main")
    assert main is not None
    await main.evaluate("() => document.forms[0].submit()")
    blocked = await _blocked(surface)
    assert [(b.method, b.reason) for b in blocked] == [("POST", "irreversible_without_approval")]
    assert not main.url.endswith("/share/posted")
    assert _money_market_count(lantern) == 0


async def test_an_approval_posts_once_even_on_a_double_click(
    open_surface: Any, lantern: Bank
) -> None:
    surface: PlaywrightSurface = await open_surface(lantern)
    await _to_review(surface, lantern)
    token = ApprovalToken(RUN_ID, "post_share")
    surface.policy_ctx = replace(surface.policy_ctx, approvals=frozenset({token}))
    result = await act_on(surface, button("Post"), "double_click", step_id="post_share")
    assert result.ok, result
    await surface.page.wait_for_timeout(1000)
    assert _money_market_count(lantern) == 1


async def test_an_approval_is_spent_by_its_first_request(open_surface: Any, lantern: Bank) -> None:
    """The bank has no double-post guard, and the guard sends requests itself, so a browser
    cancelling the first submit would not stop it. One token, one irreversible request."""
    surface: PlaywrightSurface = await open_surface(lantern)
    await _to_review(surface, lantern)
    token = ApprovalToken(RUN_ID, "post_share")
    surface.policy_ctx = replace(
        surface.policy_ctx, step_id="post_share", approvals=frozenset({token})
    )
    main = surface.page.frame(name="main")
    assert main is not None
    # Two submits back to back, under one approved binding.
    await main.evaluate(
        "() => { const f = document.forms[0];"
        " fetch(f.action, {method: 'POST', body: new URLSearchParams(new FormData(f))});"
        " fetch(f.action, {method: 'POST', body: new URLSearchParams(new FormData(f))}); }"
    )
    blocked = await _blocked(surface)
    assert [b.reason for b in blocked] == ["approval_spent"]
    await surface.page.wait_for_timeout(300)
    assert _money_market_count(lantern) == 1


async def test_a_fetch_redirecting_to_a_denied_route_is_blocked(open_site: Any, site: Site) -> None:
    """Playwright never shows a route handler the targets of a redirect; the guard checks them."""
    surface: PlaywrightSurface = await open_site("keystrokes.html")
    status = await surface.page.main_frame.evaluate(
        "() => fetch('/redirect?to=%2F__admin%2Ffaults')"
        ".then(r => 'reached ' + r.url, () => 'blocked')"
    )
    assert status == "blocked"
    assert "GET /__admin/faults" not in site.hits
    assert [b.reason for b in surface.guard.drain()] == ["route_denied"]


async def test_a_fetch_redirecting_somewhere_allowed_follows_every_hop(
    open_site: Any, site: Site
) -> None:
    surface: PlaywrightSurface = await open_site("keystrokes.html")
    body = await surface.page.main_frame.evaluate(
        "() => fetch('/redirect?to=%2Fredirect%3Fto%3D%252Fok').then(r => r.text())"
    )
    assert "reached /ok" in body
    assert surface.guard.drain() == []


async def test_a_navigation_redirecting_to_a_denied_route_is_blocked(
    open_site: Any, site: Site
) -> None:
    surface: PlaywrightSurface = await open_site("keystrokes.html")
    with contextlib.suppress(PlaywrightError):
        await surface.page.goto(site.url + "/redirect?to=%2F__admin%2Fx")
    blocked = await _blocked(surface)
    assert [b.url.rsplit("/", 2)[-2:] for b in blocked] == [["__admin", "x"]]
    assert "GET /__admin/x" not in site.hits


async def test_a_navigation_redirecting_somewhere_allowed_arrives_through_the_guard(
    open_site: Any, site: Site
) -> None:
    surface: PlaywrightSurface = await open_site("keystrokes.html")
    await surface.page.goto(site.url + "/redirect?to=%2Fwelcome")
    await surface.page.wait_for_url("**/welcome")
    assert "GET /welcome" in site.hits
    assert surface.guard.drain() == []


async def test_a_method_preserving_redirect_of_a_post_navigation_is_blocked(
    open_site: Any, site: Site
) -> None:
    surface: PlaywrightSurface = await open_site("keystrokes.html")
    await surface.page.main_frame.evaluate(
        "() => { const f = document.createElement('form'); f.method = 'POST';"
        " f.action = '/redirect?to=%2Felsewhere&code=307';"
        " document.body.appendChild(f); f.submit(); }"
    )
    assert [b.reason for b in await _blocked(surface)] == ["method_preserving_redirect"]
    assert "POST /elsewhere" not in site.hits


async def test_signing_on_through_the_guard_keeps_the_session(
    open_surface: Any, lantern: Bank
) -> None:
    """POST /signon answers 303 /frames and sets the session cookie: the cookie must survive the
    guard fetching the request itself, and the frameset must load through the guard."""
    surface: PlaywrightSurface = await open_surface(lantern)
    assert await surface.check(pred({"text_present": {"frame": "main", "text": "Teller Home"}}))
    assert surface.page.url.endswith("/frames")
    assert surface.guard.drain() == []

"""The network guard: the second enforcement layer, independent of Surface.act. Every request,
whatever caused it, is checked against the policy before it leaves the browser."""

import asyncio
import contextlib
from typing import Any

from playwright.async_api import Error as PlaywrightError

from eagleview.surfaces.web.network_guard import BlockedRequest
from eagleview.surfaces.web.playwright_surface import PlaywrightSurface
from tests.support.bank import Bank
from tests.surfaces.conftest import Events, Site
from tests.surfaces.helpers import pred


async def _blocked(surface: PlaywrightSurface, timeout_s: float = 5.0) -> list[BlockedRequest]:
    """Wait for the guard to record a block; requests leave the page asynchronously."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while not surface.guard.blocked:
        assert loop.time() < deadline, "nothing was blocked"
        await asyncio.sleep(0.05)
    return surface.guard.drain()


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
    assert (
        await main.evaluate(
            "() => fetch('/%5F%5Fadmin/faults').then(() => 'reached', () => 'blocked')"
        )
        == "blocked"
    )


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
    from tests.surfaces.test_act import _to_review

    surface: PlaywrightSurface = await open_surface(lantern)
    await _to_review(surface, lantern)
    main = surface.page.frame(name="main")
    assert main is not None
    await main.evaluate("() => document.forms[0].submit()")
    blocked = await _blocked(surface)
    assert [(b.method, b.reason) for b in blocked] == [("POST", "irreversible_without_approval")]
    assert not main.url.endswith("/share/posted")
    # Nothing was posted: the member still has exactly the shares it started with.
    with lantern.signed_on_client() as c:
        page = c.post("/inq", data={"txtA": lantern.demo, "txtB": ""})
        assert "Money Market" not in page.text


async def test_redirects_are_checked_hop_by_hop(open_site: Any, site: Site) -> None:
    """Playwright never shows a route handler the targets of a redirect, so an allowed URL that
    answers 302 to /__admin would otherwise get through. Navigations and fetches alike."""
    surface: PlaywrightSurface = await open_site("keystrokes.html")
    main = surface.page.main_frame
    # A fetch that redirects to a denied route.
    status = await main.evaluate(
        "() => fetch('/redirect?to=%2F__admin%2Ffaults')"
        ".then(r => 'reached ' + r.url, () => 'blocked')"
    )
    assert status == "blocked"
    assert "GET /__admin/faults" not in site.hits
    # A fetch that redirects somewhere allowed still works, through every hop.
    ok = await main.evaluate("() => fetch('/redirect?to=%2Fok').then(r => r.text())")
    assert "reached /ok" in ok
    # A navigation that redirects somewhere allowed lands there, as a fresh, routed request.
    await surface.page.goto(site.url + "/redirect?to=%2Fwelcome")
    await surface.page.wait_for_url("**/welcome")
    assert "GET /welcome" in site.hits
    # A navigation that redirects to a denied route: aborted, and the browser shows its error page.
    with contextlib.suppress(PlaywrightError):
        await surface.page.goto(site.url + "/redirect?to=%2F__admin%2Fx")
    assert "GET /__admin/x" not in site.hits
    assert any(b.url.endswith("/__admin/x") for b in surface.guard.drain())


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

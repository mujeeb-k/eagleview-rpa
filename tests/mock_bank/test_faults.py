"""Every fault from PLAN.md P1, reproduced against the running app."""

import time

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, expect

from mock_bank.seed import DEMO_MEMBER
from tests.mock_bank.conftest import Bank, main_frame
from tests.mock_bank.teller import open_member, search, sign_on

# ---- session_expire_after_requests ------------------------------------------------------------


def test_session_expiry_counts_authenticated_page_requests(lantern: Bank) -> None:
    with lantern.signed_on_client() as c:
        lantern.set_faults(session_expire_after_requests="2")
        assert c.get("/img/go.svg").status_code == 200  # static: not counted
        assert "Teller Home" in c.get("/home").text  # 1
        assert "Member Search" in c.get("/inq").text  # 2
        assert "SESSION EXPIRED" in c.get("/home").text  # 3: expired
        assert "SESSION EXPIRED" in c.get("/home").text  # the session is gone for good
    with lantern.admin() as admin:
        assert admin.get("/__admin/faults").json() == {"faults": {}}  # one-shot


def test_frameset_load_counts_as_four_requests(lantern: Bank) -> None:
    with lantern.signed_on_client() as c:
        lantern.set_faults(session_expire_after_requests="4")
        for path in ("/frames", "/banner", "/nav", "/home"):
            assert "SESSION EXPIRED" not in c.get(path).text
        assert "SESSION EXPIRED" in c.get("/inq").text


async def test_session_expires_mid_flow_and_sign_on_recovers(page: Page, lantern: Bank) -> None:
    await sign_on(page, lantern)
    lantern.set_faults(session_expire_after_requests="2")
    # GET /inq is request 1, POST /inq is 2, so GET /member is the one that expires.
    main = await search(page, lantern, DEMO_MEMBER)
    await expect(main.get_by_text("SESSION EXPIRED")).to_be_visible()
    await main.get_by_role("link", name="Sign On").click()  # target=_top leaves the frameset
    await sign_on(page, lantern)
    await open_member(page, lantern, DEMO_MEMBER)


# ---- system_notice_on / unknown_modal_on -------------------------------------------------------


async def _behind_modal_is_blocked(page: Page) -> None:
    with pytest.raises(PlaywrightError, match="intercepts pointer events"):
        await main_frame(page).get_by_role("button", name="Open Share").click(timeout=1500)


async def test_system_notice_blocks_until_ok(page: Page, bank: Bank) -> None:
    bank.set_faults(system_notice_on="/member")
    await sign_on(page, bank)
    main = await open_member(page, bank, DEMO_MEMBER)
    await expect(main.get_by_text("System Notice")).to_be_visible()
    await _behind_modal_is_blocked(page)
    await main.get_by_role("button", name="OK").click()
    await expect(main.get_by_text("System Notice")).to_be_hidden()
    await main.get_by_role("button", name="Open Share").click()
    await expect(main.get_by_text("Open Share Account")).to_be_visible()


async def test_unknown_modal_needs_a_human_step(page: Page, lantern: Bank) -> None:
    lantern.set_faults(unknown_modal_on="/member")
    await sign_on(page, lantern)
    main = await open_member(page, lantern, DEMO_MEMBER)
    await expect(main.get_by_text("Cash Drawer Alert")).to_be_visible()
    assert await main.get_by_text("System Notice").count() == 0
    await _behind_modal_is_blocked(page)
    await expect(main.get_by_role("button", name="Continue")).to_be_disabled()
    await main.get_by_role("checkbox").check()
    await main.get_by_role("button", name="Continue").click()
    await expect(main.get_by_text("Cash Drawer Alert")).to_be_hidden()
    await main.get_by_role("button", name="Open Share").click()
    await expect(main.get_by_text("Open Share Account")).to_be_visible()


async def test_modal_only_on_its_route(page: Page, lantern: Bank) -> None:
    lantern.set_faults(system_notice_on="/member")
    await sign_on(page, lantern)
    main = await search(page, lantern, "99999")
    await expect(main.get_by_text("Member Search")).to_be_visible()
    assert await main.get_by_text("System Notice").count() == 0


# ---- slow_ms ---------------------------------------------------------------------------------


async def test_slow_route_settles(page: Page, lantern: Bank) -> None:
    lantern.set_faults(slow_ms="/member:1200")
    await sign_on(page, lantern)
    start = time.monotonic()
    main = await search(page, lantern, DEMO_MEMBER)
    await expect(main.get_by_text("Member Detail")).to_be_visible()
    assert time.monotonic() - start >= 1.2


def test_slow_route_does_not_block_other_routes(lantern: Bank) -> None:
    lantern.set_faults(slow_ms="/inq:800")
    with lantern.signed_on_client() as c:
        start = time.monotonic()
        c.get("/home")
        assert time.monotonic() - start < 0.5
        c.get("/inq")
        assert time.monotonic() - start >= 0.8


# ---- error500_on -------------------------------------------------------------------------------


async def test_error500_once_then_recovers(page: Page, lantern: Bank) -> None:
    lantern.set_faults(error500_on="/member:once")
    await sign_on(page, lantern)
    main = await search(page, lantern, DEMO_MEMBER)
    await expect(main.get_by_text("SYSTEM ERROR")).to_be_visible()
    await expect(main.get_by_text("IBT-0500-00001")).to_be_visible()
    frame = page.frame(name="main")
    assert frame is not None
    await frame.goto(lantern.url + "/member")  # retry the same request
    await expect(main.get_by_text("Member Detail")).to_be_visible()


def test_error500_always(lantern: Bank) -> None:
    lantern.set_faults(error500_on="/inq")
    with lantern.signed_on_client() as c:
        for _ in range(3):
            r = c.get("/inq")
            assert r.status_code == 500 and "SYSTEM ERROR" in r.text
        assert c.get("/home").status_code == 200


# ---- role=inquiry_only -------------------------------------------------------------------------


async def test_inquiry_only_can_look_up_but_not_open(page: Page, bank: Bank) -> None:
    bank.set_faults(role="inquiry_only")
    await sign_on(page, bank)
    main = await open_member(page, bank, DEMO_MEMBER)
    await expect(main.get_by_text("Share Accounts")).to_be_visible()
    await main.get_by_role("button", name="Open Share").click()
    await expect(main.get_by_text("IBT-0403 ACCESS DENIED")).to_be_visible()


def test_inquiry_only_cannot_post(lantern: Bank) -> None:
    with lantern.signed_on_client() as c:
        c.post("/inq", data={"txtA": DEMO_MEMBER})
        lantern.set_faults(role="inquiry_only")
        r = c.post("/share/post", data={"f3": "170", "f4": "", "f5": "Monthly"})
        assert "ACCESS DENIED" in r.text
        lantern.set_faults(role="teller")
        r = c.post("/share/post", data={"f3": "170", "f4": "", "f5": "Monthly"})
        assert "Share Account Opened" in r.text


# ---- maintenance (extra: the app profile's maintenance_banner state needs a trigger) -----------


def test_maintenance(lantern: Bank) -> None:
    lantern.set_faults(maintenance="on")
    with lantern.admin() as c:
        r = c.get("/signon")
        assert r.status_code == 503 and "SYSTEM UNAVAILABLE" in r.text
    lantern.set_faults(maintenance="off")
    with lantern.admin() as c:
        assert c.get("/signon").status_code == 200


# ---- admin API ---------------------------------------------------------------------------------


def test_admin_rejects_bad_specs_and_clears(lantern: Bank) -> None:
    with lantern.admin() as c:
        r = c.post("/__admin/faults", json={"slow_ms": "/inq"})
        assert r.status_code == 400 and "slow_ms" in r.json()["error"]
        assert c.post("/__admin/faults", json=["nope"]).status_code == 400
        c.post("/__admin/faults", json={"role": "inquiry_only", "system_notice_on": "/inq"})
        assert len(c.get("/__admin/faults").json()["faults"]) == 2
        assert c.delete("/__admin/faults").json() == {"faults": {}}


def test_reset_restores_seed_state(lantern: Bank) -> None:
    with lantern.signed_on_client() as c:
        c.post("/inq", data={"txtA": DEMO_MEMBER})
        c.post("/share/post", data={"f3": "170", "f4": "", "f5": "Monthly"})
        assert "S50" in c.get("/member").text
    with lantern.admin() as admin:
        admin.post("/__admin/reset").raise_for_status()
    with lantern.signed_on_client() as c:
        c.post("/inq", data={"txtA": DEMO_MEMBER})
        assert "S50" not in c.get("/member").text
        r = c.post("/share/post", data={"f3": "170", "f4": "", "f5": "Monthly"})
        assert str(lantern.tenant.txn_base) in r.text  # transaction numbers restart too


async def test_modal_occludes_but_does_not_remove_text(page: Page, lantern: Bank) -> None:
    # Pins the behaviour recorded in DECISIONS.md: DOM visibility alone cannot see the modal, so
    # Eagleview's visibility checks must hit-test. If this ever fails, revisit that decision.
    lantern.set_faults(unknown_modal_on="/member")
    await sign_on(page, lantern)
    main = await open_member(page, lantern, DEMO_MEMBER)
    await expect(main.get_by_text("Cash Drawer Alert")).to_be_visible()
    await expect(main.get_by_text("Share Accounts")).to_be_visible()  # Playwright: "visible"
    frame = page.frame(name="main")
    assert frame is not None
    topmost = await frame.evaluate(
        """() => {
            const cell = [...document.querySelectorAll('b')]
                .find(b => b.textContent === 'Share Accounts');
            const r = cell.getBoundingClientRect();
            return cell.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2));
        }"""
    )
    assert topmost is False  # hit-testing sees the overlay on top

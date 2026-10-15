"""Playwright smoke tests: a scripted teller walks both flows on both tenants. No LLM."""

import re
from decimal import Decimal

from playwright.async_api import BrowserContext, Page, expect

from mock_bank.app import money
from mock_bank.seed import generate
from tests.mock_bank.conftest import Bank, main_frame
from tests.mock_bank.teller import open_member, search, sign_on

NOT_FOUND = re.compile(r"no member(s)? found", re.I)


async def test_lookup_share_balances(page: Page, bank: Bank) -> None:
    await sign_on(page, bank)
    main = await open_member(page, bank, bank.demo)
    expected = generate(bank.tenant)[bank.demo]
    await expect(main.get_by_text(expected.display_name)).to_be_visible()
    await expect(main.get_by_text(expected.masked_ssn)).to_be_visible()
    for share in expected.shares:
        row = main.locator("tr", has_text=share.description).filter(has_text=share.suffix)
        await expect(row).to_contain_text(money(share.balance))
        await expect(row).to_contain_text(money(share.available))
    # The raw SSN never reaches the screen.
    assert expected.ssn not in await main.locator("body").inner_text()


async def test_member_not_found(page: Page, bank: Bank) -> None:
    await sign_on(page, bank)
    main = await search(page, bank, "99999")
    await expect(main.get_by_text(NOT_FOUND)).to_be_visible()


async def test_other_tenants_demo_member_is_not_found(
    context: BrowserContext, lantern: Bank, copperhill: Bank
) -> None:
    # Credit unions never share members: each tenant's demo member is unknown at the other.
    # This is the check that would catch a run routed to the wrong tenant.
    for here, there in [(copperhill, lantern), (lantern, copperhill)]:
        page = await context.new_page()
        await sign_on(page, here)
        main = await search(page, here, there.demo)
        await expect(main.get_by_text(NOT_FOUND)).to_be_visible()
        await expect(main.get_by_text(there.demo)).to_be_visible()


async def test_invalid_member_number(page: Page, bank: Bank) -> None:
    await sign_on(page, bank)
    main = await search(page, bank, "10A82")
    await expect(main.get_by_text("IBT-1002 INVALID MEMBER NUMBER")).to_be_visible()


async def test_open_share_validate_review_post(page: Page, bank: Bank) -> None:
    money_market = bank.tenant.share_types["money_market"]
    suffix = bank.tenant.suffix(money_market.low)
    await sign_on(page, bank)
    main = await open_member(page, bank, bank.demo)
    await main.get_by_role("button", name="Open Share").click()
    await expect(main.get_by_text("Open Share Account")).to_be_visible()
    await main.locator("select[name='f3']").select_option(label=money_market.description)
    await main.locator("input[name='f4']").fill("Rainy Day")
    await main.locator("input[name='f5'][value='Quarterly']").check()
    await main.get_by_role("button", name="Validate").click()

    await expect(main.get_by_text("Review - Open Share")).to_be_visible()
    await expect(main.locator("tr", has_text="Suffix")).to_contain_text(suffix)
    await main.get_by_role("button", name="Post").click()

    await expect(main.get_by_text("Share Account Opened")).to_be_visible()
    await expect(main.locator("tr", has_text="Transaction #:")).to_contain_text(
        str(bank.tenant.txn_base)
    )
    await main.get_by_role("button", name="Member Detail").click()
    row = main.locator("tr", has_text=money_market.description)
    await expect(row).to_contain_text(suffix)
    await expect(row).to_contain_text("Rainy Day")
    await expect(row).to_contain_text(money(Decimal("0.00")))


async def test_open_share_validation_errors(page: Page, bank: Bank) -> None:
    club = bank.tenant.share_types["club"].description
    await sign_on(page, bank)
    main = await open_member(page, bank, bank.demo)
    await main.get_by_role("button", name="Open Share").click()
    await main.locator("select[name='f3']").select_option(label=club)
    await main.locator("input[name='f4']").fill("x" * 21)
    await main.get_by_role("button", name="Validate").click()
    duplicate = f"IBT-2210 MEMBER ALREADY HAS A {club.upper()} SHARE"
    await expect(main.get_by_text(duplicate)).to_be_visible()
    await expect(main.get_by_text("IBT-2214 NICKNAME EXCEEDS 20 CHARACTERS")).to_be_visible()
    # Still on the form, with the entered values kept.
    await expect(main.locator("input[name='f4']")).to_have_value("x" * 21)


async def test_double_post_opens_two_shares(page: Page, lantern: Bank) -> None:
    # No duplicate-submission guard, by design: commit safety must come from the client.
    await sign_on(page, lantern)
    main = await open_member(page, lantern, lantern.demo)
    await main.get_by_role("button", name="Open Share").click()
    await main.locator("select[name='f3']").select_option(label="Money Market")
    await main.get_by_role("button", name="Validate").click()
    frame = page.frame(name="main")
    assert frame is not None
    for txn in (lantern.tenant.txn_base, lantern.tenant.txn_base + 1):
        await main.get_by_role("button", name="Post").click()
        await expect(main.locator("tr", has_text="Transaction #:")).to_contain_text(str(txn))
        await frame.goto(lantern.url + "/share/review")  # the teller goes back and posts again
    await frame.goto(lantern.url + "/member")
    await expect(main.locator("tr", has_text="Money Market")).to_have_count(2)


async def test_sign_off(page: Page, bank: Bank) -> None:
    await sign_on(page, bank)
    await main_frame(page).locator("body").wait_for()
    await page.frame_locator("frame[name='nav']").get_by_role("link", name="Sign Off").click()
    await expect(page.get_by_text("Teller Sign On")).to_be_visible()


async def test_one_browser_holds_both_tenants(
    context: BrowserContext, lantern: Bank, copperhill: Bank
) -> None:
    # Cookies are per host, not per port: distinct cookie names keep the sessions apart.
    a, b = await context.new_page(), await context.new_page()
    await sign_on(a, lantern)
    await sign_on(b, copperhill)
    await open_member(a, lantern, lantern.demo)
    await open_member(b, copperhill, copperhill.demo)

"""A scripted teller: drives the mock bank through Playwright the way a person would. No LLM."""

from playwright.async_api import FrameLocator, Page, expect

from tests.mock_bank.conftest import Bank, main_frame, nav_frame


async def sign_on(page: Page, bank: Bank) -> None:
    await page.goto(bank.url + "/signon")
    await page.locator("input[name='f1']").fill(bank.tenant.username)
    await page.locator("input[name='f2']").fill(bank.password)
    await page.locator("input[type='submit']").click()
    if bank.tenant.select_branch:
        await expect(page.get_by_text("Select Branch")).to_be_visible()
        await page.locator("select[name='br']").select_option(index=0)
        await page.get_by_role("button", name="Continue").click()
    # All three frames, not just main: banner and nav requests count toward session expiry, so a
    # caller arming session_expire_after_requests must know they have already been served.
    await expect(main_frame(page).get_by_text("Teller Home")).to_be_visible()
    await expect(page.frame_locator("frame[name='banner']").get_by_text("Sign Off")).to_be_visible()
    await expect(nav_frame(page).get_by_text("FUNCTIONS")).to_be_visible()


async def search(page: Page, bank: Bank, member: str) -> FrameLocator:
    await nav_frame(page).get_by_role("link", name=bank.tenant.labels.member_inquiry).click()
    main = main_frame(page)
    await expect(main.get_by_text("Member Search")).to_be_visible()
    await main.locator("input[name='txtA']").fill(member)
    if bank.tenant.search_button == "image":
        await main.locator("img[src*='go']").click()
    else:
        await main.get_by_role("button", name="Search").click()
    return main


async def open_member(page: Page, bank: Bank, member: str) -> FrameLocator:
    main = await search(page, bank, member)
    await expect(main.get_by_text("Member Detail")).to_be_visible()
    return main

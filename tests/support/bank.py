"""Mock-bank fixtures, shared as a pytest plugin: both tenants in-process on ephemeral ports, reset
before every test, plus a pinned-geometry browser."""

import secrets
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import httpx2 as httpx
import pytest
from playwright.async_api import Browser, BrowserContext, FrameLocator, Page, async_playwright

from mock_bank.config import ShareKind, Tenant, all_tenants
from mock_bank.server import BankThread

VIEWPORT = {"width": 1280, "height": 800}


@dataclass
class Bank:
    tenant: Tenant
    url: str
    password: str

    @property
    def demo(self) -> str:
        return self.tenant.demo_member

    def product(self, kind: ShareKind) -> str:
        """The Open Share select code for a share kind at this tenant."""
        return next(code for code, k in self.tenant.products.items() if k == kind)

    def admin(self) -> httpx.Client:
        return httpx.Client(base_url=self.url, timeout=10)

    def set_faults(self, **specs: str) -> None:
        with self.admin() as c:
            r = c.post("/__admin/faults", json=specs)
            assert r.status_code == 200, r.text

    @contextmanager
    def signed_on_client(self) -> Iterator[httpx.Client]:
        """An HTTP client with a live session (branch already chosen where the tenant asks)."""
        with httpx.Client(base_url=self.url, follow_redirects=True, timeout=10) as c:
            c.post("/signon", data={"f1": self.tenant.username, "f2": self.password})
            if self.tenant.select_branch:
                c.post("/branch", data={"br": self.tenant.branches[0]})
            yield c


Banks = tuple[BankThread, dict[str, str]]


@pytest.fixture(scope="session")
def bank_thread() -> Iterator[Banks]:
    # One random password per tenant per test session; never a committed value.
    passwords = {t.slug: secrets.token_urlsafe(16) for t in all_tenants()}
    thread = BankThread(all_tenants(), passwords).start()
    yield thread, passwords
    thread.stop()


def _bank(bank_thread: Banks, slug: str) -> Bank:
    thread, passwords = bank_thread
    running = thread.running[slug]
    bank = Bank(running.tenant, running.url, passwords[slug])
    with bank.admin() as c:
        c.post("/__admin/reset").raise_for_status()
    return bank


@pytest.fixture
def lantern(bank_thread: Banks) -> Bank:
    return _bank(bank_thread, "lantern-bay")


@pytest.fixture
def copperhill(bank_thread: Banks) -> Bank:
    return _bank(bank_thread, "copperhill")


@pytest.fixture(params=["lantern-bay", "copperhill"])
def bank(request: pytest.FixtureRequest, bank_thread: Banks) -> Bank:
    return _bank(bank_thread, request.param)


@pytest.fixture
async def browser() -> AsyncIterator[Browser]:
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        yield b
        await b.close()


@pytest.fixture
async def context(browser: Browser) -> AsyncIterator[BrowserContext]:
    ctx = await browser.new_context(viewport=VIEWPORT, device_scale_factor=1)  # type: ignore[arg-type]
    ctx.set_default_timeout(5000)
    yield ctx
    await ctx.close()


@pytest.fixture
async def page(context: BrowserContext) -> Page:
    return await context.new_page()


def main_frame(page: Page) -> FrameLocator:
    return page.frame_locator("frame[name='main']")


def nav_frame(page: Page) -> FrameLocator:
    return page.frame_locator("frame[name='nav']")

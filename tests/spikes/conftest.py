"""Shared fixtures for the P0 spikes: a static page server and a pinned-geometry browser."""

import functools
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Browser, BrowserContext, async_playwright

PAGES = Path(__file__).parent / "pages"

# 1280x800 at device scale 1, so screenshot pixels, CSS pixels and CDP coordinates all agree.
# Pinned explicitly: a headed run on a Retina display would otherwise default to scale 2.
VIEWPORT = {"width": 1280, "height": 800}


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture(scope="session")
def pages_url() -> Iterator[str]:
    handler = functools.partial(_QuietHandler, directory=str(PAGES))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
async def browser() -> AsyncIterator[Browser]:
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        yield b
        await b.close()


async def new_context(browser: Browser) -> BrowserContext:
    return await browser.new_context(viewport=VIEWPORT, device_scale_factor=1)  # type: ignore[arg-type]


@pytest.fixture
async def context(browser: Browser) -> AsyncIterator[BrowserContext]:
    ctx = await new_context(browser)
    yield ctx
    await ctx.close()

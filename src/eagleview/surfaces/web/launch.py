"""Starting a browser and opening one PlaywrightSurface per run. Replay itself never imports
Playwright: it receives a `SurfaceOpener` and works against the Surface protocol."""

from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

from playwright.async_api import Browser, async_playwright

from eagleview.safety.policy_engine import PolicyContext, PolicyEngine
from eagleview.surfaces.base import EventSink, HolderGuard, Surface, SurfaceOpener
from eagleview.surfaces.web.playwright_surface import PlaywrightSurface

# --headed: slow enough for a person to follow. Playwright inserts the delay inside each browser
# operation, so replay's own code waits only on predicates, headed or not.
HEADED_SLOW_MO_MS = 250


@asynccontextmanager
async def launched_browser(*, headed: bool = False) -> AsyncIterator[Browser]:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=not headed, slow_mo=HEADED_SLOW_MO_MS if headed else 0
        )
        try:
            yield browser
        finally:
            await browser.close()


def surface_opener(
    browser: Browser,
    engine: PolicyEngine,
    *,
    sink: EventSink | None = None,
    label_aliases: Mapping[str, Sequence[str]] | None = None,
    record_video_dir: Path | None = None,
) -> SurfaceOpener:
    def open_surface(
        holder: HolderGuard, ctx: PolicyContext
    ) -> AbstractAsyncContextManager[Surface]:
        @asynccontextmanager
        async def opened() -> AsyncIterator[Surface]:
            surface = await PlaywrightSurface.open(
                browser,
                engine,
                ctx,
                holder,
                sink=sink,
                label_aliases=label_aliases,
                record_video_dir=record_video_dir,
            )
            try:
                yield surface
            finally:
                await surface.close()

        return opened()

    return open_surface

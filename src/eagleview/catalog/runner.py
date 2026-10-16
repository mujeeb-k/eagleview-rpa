"""Running a catalog tool: an unattended replay on one configured tenant, with its overlays.
The browser is shared across calls for the life of the catalog."""

from collections.abc import AsyncGenerator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from playwright.async_api import Browser

from eagleview.catalog.registry import Entry
from eagleview.replay.preflight import RunOptions
from eagleview.runner import RUNS, run_replay
from eagleview.schema.app_profile import AppProfile
from eagleview.schema.overlay import Overlay
from eagleview.schema.policy import Policy
from eagleview.schema.result import RunResult
from eagleview.schema.tenant import TenantProfile
from eagleview.surfaces.web.launch import launched_browser


class UnknownTool(KeyError):
    pass


@dataclass
class Catalog:
    entries: list[Entry]
    profile: AppProfile
    tenant: TenantProfile
    policy: Policy
    overlays: list[Overlay] = field(default_factory=list[Overlay])
    runs_root: Path = RUNS
    environ: Mapping[str, str] | None = None
    browser: Browser | None = None

    def entry(self, tool: str) -> Entry:
        for e in self.entries:
            if e.tool_name == tool:
                return e
        raise UnknownTool(tool)

    async def call(self, tool: str, arguments: Mapping[str, object]) -> RunResult:
        """An agent's call is unattended: approved capabilities only, and never self-approving."""
        return await run_replay(
            self.entry(tool).capability,
            dict(arguments),
            profile=self.profile,
            tenant=self.tenant,
            policy=self.policy,
            options=RunOptions(unattended=True),
            browser=self.browser,
            runs_root=self.runs_root,
            environ=self.environ,
            overlays=self.overlays,
        )


@asynccontextmanager
async def open_catalog(catalog: Catalog) -> AsyncGenerator[Catalog]:
    async with AsyncExitStack() as stack:
        if catalog.browser is None:
            catalog.browser = await stack.enter_async_context(launched_browser())
        yield catalog

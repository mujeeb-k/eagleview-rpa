"""Fixtures for the web surface: a PlaywrightSurface on a mock-bank tenant, with the default policy
pointed at that tenant's ephemeral origin; a small static site for pages the bank doesn't have
(keystroke handlers, redirects); and an event sink that keeps what the surface logs."""

import threading
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import pytest
from playwright.async_api import Browser

from eagleview.artifacts.yaml_io import load_policy
from eagleview.safety.policy_engine import PolicyContext, PolicyEngine
from eagleview.schema.policy import Policy
from eagleview.surfaces.base import FixedHolder
from eagleview.surfaces.web.playwright_surface import PlaywrightSurface
from tests.fixtures import POLICY
from tests.support.bank import Bank
from tests.support.replay import policy_for
from tests.support.teller import sign_on

RUN_ID = "r_surface_test"
EPOCH = 1


@dataclass
class Events:
    records: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

    def event(self, kind: str, actor: str, **fields: object) -> object:
        self.records.append({"kind": kind, "actor": actor, **fields})
        return None

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [r for r in self.records if r["kind"] == kind]


OpenSurface = Callable[..., Any]


@pytest.fixture
def events() -> Events:
    return Events()


@pytest.fixture
async def open_surface(browser: Browser, events: Events) -> AsyncIterator[Callable[..., Any]]:
    opened: list[PlaywrightSurface] = []

    async def _open(
        bank: Bank,
        *,
        actor: str = "automation",
        mode: str = "replay",
        policy: Policy | None = None,
        signed_on: bool = True,
        **kwargs: Any,
    ) -> PlaywrightSurface:
        engine = PolicyEngine(policy or policy_for(bank))
        ctx = PolicyContext(tenant=bank.tenant.slug, mode=mode, run_id=RUN_ID)  # type: ignore[arg-type]
        surface = await PlaywrightSurface.open(
            browser, engine, ctx, FixedHolder(actor, EPOCH), sink=events, **kwargs
        )
        opened.append(surface)
        if signed_on:
            await sign_on(surface.page, bank)  # the harness signs on; the LLM never does
        return surface

    yield _open
    for s in opened:
        await s.close()


PAGES = Path(__file__).parent / "pages"
SITE_TENANT = "testsite"


@dataclass
class Site:
    url: str
    hits: list[str]


class _SiteHandler(BaseHTTPRequestHandler):
    """Static pages, plus redirects: /redirect?to=<path>&code=<status>."""

    hits: list[str]

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _serve(self) -> None:
        self.hits.append(f"{self.command} {self.path}")
        path, _, query = self.path.partition("?")
        if path == "/redirect":
            params = {k: v[0] for k, v in parse_qs(query).items()}
            self.send_response(int(params.get("code", "302")))
            self.send_header("Location", params["to"])
            self.end_headers()
            return
        file = PAGES / path.lstrip("/")
        if path.endswith(".html") and file.is_file():
            body = file.read_bytes()
        else:
            body = f"<html><body><b>reached {path}</b></body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _serve
    do_POST = _serve


@pytest.fixture(scope="session")
def site_server() -> Iterator[Site]:
    hits: list[str] = []
    handler = type("Handler", (_SiteHandler,), {"hits": hits})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield Site(f"http://127.0.0.1:{server.server_address[1]}", hits)
    server.shutdown()
    server.server_close()


@pytest.fixture
def site(site_server: Site) -> Site:
    site_server.hits.clear()
    return site_server


@pytest.fixture
async def open_site(
    browser: Browser, events: Events, site: Site
) -> AsyncIterator[Callable[..., Any]]:
    """A surface for the static site, as tenant `testsite`, starting on `page`."""
    opened: list[PlaywrightSurface] = []

    async def _open(page: str, *, actor: str = "automation", **kwargs: Any) -> PlaywrightSurface:
        policy = load_policy(POLICY).model_copy(
            update={"allowed_origins": {SITE_TENANT: [site.url]}}
        )
        ctx = PolicyContext(tenant=SITE_TENANT, mode="replay", run_id=RUN_ID)
        surface = await PlaywrightSurface.open(
            browser, PolicyEngine(policy), ctx, FixedHolder(actor, EPOCH), sink=events, **kwargs
        )
        opened.append(surface)
        await surface.page.goto(f"{site.url}/{page}")
        return surface

    yield _open
    for s in opened:
        await s.close()

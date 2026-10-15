"""Spike B: stream a live page over CDP screencast to a viewer page and relay a click back.

Target (context A) is screencast with ``Page.startScreencast``; every frame is acknowledged
with ``Page.screencastFrameAck`` (Chromium stops sending after a few unacknowledged frames). An
in-process FastAPI relay pushes frames over a websocket to a viewer page in a separate context
(B), which paints them on a canvas. A click on the viewer canvas travels back over the websocket
and is dispatched to the target with ``Input.dispatchMouseEvent``. One asyncio loop, no sleeps.
"""

import asyncio
import contextlib
import json
import socket
from collections.abc import AsyncIterator
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from playwright.async_api import Browser, CDPSession

from tests.spikes.conftest import new_context

VIEWER_HTML = """<!doctype html>
<html><body style="margin:0">
<canvas id="c" width="1280" height="800"></canvas>
<script>
const ws = new WebSocket(`ws://${location.host}/ws`);
const c = document.getElementById('c');
const g = c.getContext('2d');
let frames = 0;
ws.onopen = () => { document.body.dataset.ready = '1'; };
ws.onmessage = (ev) => {
  const img = new Image();
  img.onload = () => {
    g.drawImage(img, 0, 0);
    frames += 1;
    document.body.dataset.frames = String(frames);
    document.body.dataset.size = `${img.naturalWidth}x${img.naturalHeight}`;
  };
  img.src = 'data:image/jpeg;base64,' + JSON.parse(ev.data).data;
};
c.addEventListener('click', (e) => {
  const r = c.getBoundingClientRect();
  ws.send(JSON.stringify({x: Math.round(e.clientX - r.left), y: Math.round(e.clientY - r.top)}));
});
</script>
</body></html>"""


class Relay:
    """Fans screencast frames out to viewers and dispatches viewer clicks to the target."""

    def __init__(self) -> None:
        self.cdp: CDPSession | None = None
        self.viewers: set[WebSocket] = set()
        self.metadata: list[dict[str, Any]] = []
        self.app = FastAPI()
        self.app.get("/viewer")(self._viewer)
        self.app.websocket("/ws")(self._ws)

    async def _viewer(self) -> HTMLResponse:
        return HTMLResponse(VIEWER_HTML)

    async def _ws(self, ws: WebSocket) -> None:
        await ws.accept()
        self.viewers.add(ws)
        try:
            while True:
                click = json.loads(await ws.receive_text())
                await self._click(int(click["x"]), int(click["y"]))
        except WebSocketDisconnect:
            pass
        finally:
            self.viewers.discard(ws)

    async def _click(self, x: int, y: int) -> None:
        assert self.cdp is not None
        # Screencast frames are 1:1 with the 1280x800 viewport (asserted below), so no rescaling.
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            await self.cdp.send(
                "Input.dispatchMouseEvent",
                {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1},
            )

    async def on_frame(self, params: dict[str, Any]) -> None:
        assert self.cdp is not None
        await self.cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
        self.metadata.append(params["metadata"])
        message = json.dumps({"data": params["data"]})
        for ws in list(self.viewers):
            await ws.send_text(message)


@pytest.fixture
async def relay() -> AsyncIterator[tuple[Relay, str]]:
    r = Relay()
    # Bind and listen before uvicorn starts: early connections queue in the backlog, so there is
    # no startup race to wait out.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(r.app, log_level="warning"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    yield r, f"http://127.0.0.1:{port}"
    server.should_exit = True
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_screencast_streams_and_relays_click(
    browser: Browser, pages_url: str, relay: tuple[Relay, str]
) -> None:
    r, relay_url = relay
    target_ctx, viewer_ctx = await new_context(browser), await new_context(browser)
    target = await target_ctx.new_page()
    await target.goto(f"{pages_url}/target.html")
    viewer = await viewer_ctx.new_page()
    await viewer.goto(f"{relay_url}/viewer")
    await viewer.wait_for_function("document.body.dataset.ready === '1'", timeout=5000)

    r.cdp = await target_ctx.new_cdp_session(target)
    r.cdp.on("Page.screencastFrame", lambda p: asyncio.ensure_future(r.on_frame(p)))
    await r.cdp.send(
        "Page.startScreencast",
        {"format": "jpeg", "quality": 70, "maxWidth": 1280, "maxHeight": 800, "everyNthFrame": 1},
    )

    # Frames are only produced on repaint, so drive two visible changes and wait for each frame.
    for n in (1, 2):
        await target.evaluate(f"document.getElementById('tick').textContent = 'tick {n}'")
        await viewer.wait_for_function(
            f"Number(document.body.dataset.frames || 0) >= {n}", timeout=5000
        )

    assert await viewer.evaluate("document.body.dataset.size") == "1280x800"
    meta = r.metadata[-1]
    assert (meta["deviceWidth"], meta["deviceHeight"]) == (1280, 800)
    assert meta["offsetTop"] == 0 and meta["pageScaleFactor"] == 1

    # Click the button's location as painted on the viewer canvas; it must land on the target.
    box = await target.locator("#btn").bounding_box()
    assert box is not None
    await viewer.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    await target.wait_for_function(
        "document.getElementById('out').textContent === 'clicked'", timeout=5000
    )

    await r.cdp.send("Page.stopScreencast")
    await viewer_ctx.close()
    await target_ctx.close()

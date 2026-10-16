"""The operator console (FastAPI, port 8700 by default, loopback only).

Pages: an interventions list, and a session page with the live screencast, a banner naming the
current holder and epoch, and the buttons Take Control, Pause, Resume (with a note), Approve, Deny
and Abort. Everything the page does goes through the JSON API below, the same API the scripted
test operator uses:

    GET  /api/interventions[?open=true]          GET  /api/sessions[/{run_id}]
    POST /api/sessions/{run_id}/take   {operator, force}
    POST /api/sessions/{run_id}/pause  {operator}
    POST /api/sessions/{run_id}/act    {operator, action}   -> Surface.act as operator:<name>
    POST /api/sessions/{run_id}/resume {operator, note}
    POST /api/sessions/{run_id}/abort  {operator, note}
    POST /api/interventions/{id}/approve|deny {operator, note}
    GET  /api/sessions/{run_id}/boxes  visible text with geometry, what the operator sees
    WS   /sessions/{run_id}/screencast  live JPEG frames, never written to disk

There is no authentication: operators are trusted and the console binds 127.0.0.1 (REPORT §7)."""

import html
import json
import re
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from eagleview.control.interventions import InterventionDesk, LiveSession, operator_actor
from eagleview.control.session_controller import ControlState, InvalidTransition
from eagleview.schema.action import Action

_EVIDENCE = re.compile(r"^steps/\d{4}_(before|after)\.png$")


class OperatorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operator: str
    note: str | None = None
    force: bool = False


class ActBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operator: str
    action: dict[str, Any]


def create_console(desk: InterventionDesk, runs_root: Path) -> FastAPI:
    app = FastAPI(title="Eagleview operator console", docs_url=None, redoc_url=None)

    def live(run_id: str) -> LiveSession:
        try:
            return desk.session(run_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from None

    def actor_of(body: OperatorBody | ActBody) -> str:
        try:
            return operator_actor(body.operator)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    def resolve_open(
        run_id: str, kind: Literal["takeover", "approval"], how: Any, by: str, note: str | None
    ) -> None:
        for iv in desk.open_for(run_id, kind):
            desk.resolve(iv.id, how, by, note)

    def transition(fn: Any, *args: Any) -> None:
        try:
            fn(*args)
        except InvalidTransition as exc:
            raise HTTPException(409, str(exc)) from None

    # --- JSON API -----------------------------------------------------------------------------

    @app.get("/api/interventions")
    async def interventions(open: bool = False) -> list[dict[str, Any]]:
        return [iv.model_dump(mode="json") for iv in desk.store.list(open_only=open)]

    @app.get("/api/sessions")
    async def sessions() -> list[dict[str, Any]]:
        return [
            {**s.controller.snapshot(), "capability": s.capability, "at_step": s.at_step}
            for s in desk.sessions.values()
        ]

    @app.get("/api/sessions/{run_id}")
    async def session_state(run_id: str) -> dict[str, Any]:
        s = live(run_id)
        return {
            **s.controller.snapshot(),
            "capability": s.capability,
            "at_step": s.at_step,
            "human_actions": len(s.actions),
            "open": [iv.model_dump(mode="json") for iv in desk.open_for(run_id)],
        }

    @app.post("/api/sessions/{run_id}/take")
    async def take(run_id: str, body: OperatorBody) -> dict[str, Any]:
        s, actor = live(run_id), actor_of(body)
        if s.controller.state is ControlState.AWAITING_HUMAN:
            transition(s.controller.take_control, actor)
        elif body.force:
            transition(s.controller.force_take, actor)
        else:
            raise HTTPException(409, f"nothing to take: the session is {s.controller.state}")
        return s.controller.snapshot()

    @app.post("/api/sessions/{run_id}/pause")
    async def pause(run_id: str, body: OperatorBody) -> dict[str, Any]:
        s, actor = live(run_id), actor_of(body)
        transition(s.controller.request_pause, actor)
        return s.controller.snapshot()

    @app.post("/api/sessions/{run_id}/act")
    async def act(run_id: str, body: ActBody) -> dict[str, Any]:
        actor_of(body)
        live(run_id)
        try:
            action = Action.model_validate(body.action)
        except ValidationError as exc:
            raise HTTPException(422, str(exc)) from None
        result = await desk.operator_act(run_id, body.operator, action)
        return {"status": result.status, "reason": result.reason}

    @app.post("/api/sessions/{run_id}/resume")
    async def resume(run_id: str, body: OperatorBody) -> dict[str, Any]:
        s, actor = live(run_id), actor_of(body)
        transition(s.controller.resume, actor)
        resolve_open(run_id, "takeover", "resumed", actor, body.note)
        return s.controller.snapshot()

    @app.post("/api/sessions/{run_id}/abort")
    async def abort(run_id: str, body: OperatorBody) -> dict[str, Any]:
        s, actor = live(run_id), actor_of(body)
        transition(s.controller.abort, actor)
        resolve_open(run_id, "takeover", "aborted", actor, body.note)
        resolve_open(run_id, "approval", "aborted", actor, body.note)
        return s.controller.snapshot()

    @app.post("/api/interventions/{iv_id}/{decision}")
    async def decide(iv_id: str, decision: str, body: OperatorBody) -> dict[str, Any]:
        actor = actor_of(body)
        iv = desk.store.get(iv_id)
        if iv is None:
            raise HTTPException(404, f"no intervention {iv_id}")
        if decision not in ("approve", "deny") or iv.kind != "approval":
            raise HTTPException(409, f"{decision} does not apply to a {iv.kind} intervention")
        if iv.resolution is not None:
            raise HTTPException(409, f"{iv_id} is already {iv.resolution}")
        how = "approved" if decision == "approve" else "denied"
        return desk.resolve(iv_id, how, actor, body.note).model_dump(mode="json")

    @app.get("/api/sessions/{run_id}/boxes")
    async def boxes(run_id: str) -> list[dict[str, Any]]:
        s = live(run_id)
        return [b.model_dump() for b in await s.surface.text_boxes()]

    # --- live view ----------------------------------------------------------------------------

    @app.websocket("/sessions/{run_id}/screencast")
    async def screencast(ws: WebSocket, run_id: str) -> None:
        await ws.accept()
        s = desk.sessions.get(run_id)
        start = getattr(s.surface, "start_screencast", None) if s else None
        if s is None or start is None:
            await ws.close(code=1008)
            return

        async def send(data: str) -> None:
            await ws.send_text(json.dumps({"data": data}))

        await start(send)
        try:
            while True:
                await ws.receive_text()  # the page sends nothing; this waits for disconnect
        except WebSocketDisconnect:
            pass
        finally:
            await s.surface.stop_screencast()  # type: ignore[attr-defined]

    @app.get("/evidence/{run_id}/{path:path}")
    async def evidence(run_id: str, path: str) -> FileResponse:
        if not re.fullmatch(r"r_[A-Za-z0-9_-]{1,64}", run_id) or not _EVIDENCE.fullmatch(path):
            raise HTTPException(404)
        file = runs_root / run_id / path  # written by evidence.writer, redacted
        if not file.is_file():
            raise HTTPException(404)
        return FileResponse(file, media_type="image/png")

    # --- pages --------------------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        rows = []
        for iv in reversed(desk.store.list()):
            shot = (
                f'<a href="/evidence/{iv.run_id}/{html.escape(iv.screenshot)}">screenshot</a>'
                if iv.screenshot
                else ""
            )
            live_link = (
                f'<a href="/sessions/{iv.run_id}">open session</a>'
                if iv.run_id in desk.sessions
                else "ended"
            )
            rows.append(
                "<tr>"
                + "".join(
                    f"<td>{html.escape(str(v))}</td>"
                    for v in (
                        iv.created_at.strftime("%H:%M:%S"),
                        iv.kind,
                        iv.reason,
                        iv.capability or "",
                        iv.step_id or "",
                        iv.resolution or "OPEN",
                    )
                )
                + f"<td>{live_link}</td><td>{shot}</td></tr>"
            )
        body = "".join(rows) or '<tr><td colspan="8">No interventions yet.</td></tr>'
        return INDEX.replace("{{rows}}", body)

    @app.get("/sessions/{run_id}", response_class=HTMLResponse)
    async def session_page(run_id: str) -> str:
        live(run_id)
        return SESSION.replace("{{run_id}}", html.escape(run_id))

    return app


INDEX = """<!doctype html><html><head><meta charset="utf-8"><title>Eagleview console</title>
<meta http-equiv="refresh" content="3">
<style>body{font:14px system-ui;margin:24px}td,th{padding:4px 10px;border-bottom:1px solid #ddd}
th{text-align:left}</style></head><body><h1>Interventions</h1>
<table><tr><th>When</th><th>Kind</th><th>Reason</th><th>Capability</th><th>Step</th>
<th>Status</th><th></th><th></th></tr>{{rows}}</table></body></html>"""

SESSION = """<!doctype html><html><head><meta charset="utf-8"><title>Session {{run_id}}</title>
<style>body{font:14px system-ui;margin:16px}#banner{padding:8px;background:#eef;margin-bottom:8px}
canvas{border:1px solid #999;cursor:crosshair}button{margin-right:6px}</style></head><body>
<div id="banner">Session {{run_id}}: <b id="state">…</b> · holder <b id="holder">…</b>
· epoch <b id="epoch">…</b> · step <b id="step">…</b></div>
<p>Operator <input id="op" size="12" value="operator">
<button onclick="call('take',{force:false})">Take control</button>
<button onclick="call('take',{force:true})">Force take</button>
<button onclick="call('pause',{})">Pause</button>
<button onclick="call('resume',{note:prompt('Resume note')||null})">Resume</button>
<button onclick="call('abort',{note:prompt('Why abort?')||null})">Abort</button>
<span id="approvals"></span> <span id="msg"></span></p>
<canvas id="c" width="1280" height="800" tabindex="0"></canvas>
<script>
const run = "{{run_id}}", api = `/api/sessions/${run}`;
const op = () => document.getElementById('op').value;
async function post(url, body) {
  const r = await fetch(url, {method: 'POST', headers: {'content-type': 'application/json'},
    body: JSON.stringify({operator: op(), ...body})});
  document.getElementById('msg').textContent = r.ok ? '' : (await r.json()).detail;
  return r;
}
const call = (what, body) => post(`${api}/${what}`, body);
const act = (action) => post(`${api}/act`, {action});
async function refresh() {
  const r = await fetch(api);
  if (!r.ok) { document.getElementById('state').textContent = 'ended'; return; }
  const s = await r.json();
  for (const k of ['state', 'holder', 'epoch']) {
    document.getElementById(k).textContent = s[k] ?? 'nobody';
  }
  document.getElementById('step').textContent = s.at_step ?? '-';
  const a = document.getElementById('approvals'); a.innerHTML = '';
  for (const iv of s.open.filter(i => i.kind === 'approval')) {
    for (const d of ['approve', 'deny']) {
      const b = document.createElement('button'); b.textContent = `${d} ${iv.step_id}`;
      b.onclick = () => post(`/api/interventions/${iv.id}/${d}`, {note: null}); a.appendChild(b);
    }
  }
}
setInterval(refresh, 1000); refresh();
const c = document.getElementById('c'), g = c.getContext('2d');
const ws = new WebSocket(`ws://${location.host}/sessions/${run}/screencast`);
ws.onmessage = (ev) => { const img = new Image(); img.onload = () => g.drawImage(img, 0, 0);
  img.src = 'data:image/jpeg;base64,' + JSON.parse(ev.data).data; };
c.addEventListener('click', (e) => { const r = c.getBoundingClientRect(); c.focus();
  act({type: 'click', x: Math.round(e.clientX - r.left), y: Math.round(e.clientY - r.top)}); });
c.addEventListener('keydown', (e) => { e.preventDefault();
  if (e.key.length === 1) act({type: 'type', text: e.key});
  else act({type: 'key', key: e.key === 'Enter' ? 'Return' : e.key}); });
</script></body></html>"""

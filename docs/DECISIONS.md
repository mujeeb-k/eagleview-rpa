# Decision log

Short entries, newest last: what I decided and why.

## 2026-10-15 — P0 bootstrap

**uv via Homebrew.** I installed uv 0.13.1 with `brew install uv` rather than the curl|sh installer,
so it can be audited and upgraded like any package. Python is pinned to 3.12.

**pytest-asyncio, auto mode, function-scoped loops.** Playwright async and uvicorn both need asyncio,
and the anyio plugin adds a backend choice I don't want. A browser per test (about 0.5 s) keeps tests
isolated.

**Spikes live in `tests/spikes/`, never `src/`.** They drive CDP directly, which inside the package
would break invariant 3 (only `Surface.act` touches the browser). P3 lifts the techniques behind the
Surface seam.

**Geometry is pinned to 1280×800 at `device_scale_factor=1`.** I set this explicitly on every context.
A high-DPI headed run would otherwise default to scale 2 and split screenshot pixels from CSS and CDP
coordinates.

**`httpx2`, not `httpx`.** anthropic 1.15.0 moved to `httpx2`, the maintained httpx fork. Transports,
clients and test mocks handed to the SDK must come from it, or they silently never see SDK traffic.
I declare it directly because tests import it.

**`websockets` for uvicorn.** uvicorn rejects WebSocket upgrades when no implementation is installed.
P5's console needs one too. I chose plain `websockets` over `uvicorn[standard]` to avoid uvloop and the
file watcher.

**Stub Makefile targets exit 1.** `bank`, `demo-offline` and `demo-full` print "not implemented until
Pn" and fail, so a stub never passes for a working target.

**CI is written but has never run.** `ci.yml` runs `uv sync --locked`, installs Chromium with system
deps, then `make check`. `live` tests are deselected by default, so no key is needed. The repo has no
remote yet.

## 2026-10-15 — Spike A: grounding across frames

**CDP hit-testing pierces framesets, so I built no fallback.** `DOM.getNodeForLocation` in viewport
coordinates returns the inner frame's `frameId` and node. I checked this through a two-level frameset
with the `main` frame scrolled 400 px. `Accessibility.getPartialAXTree` on that node gives role and
name. I didn't build the `elementFromPoint` fallback.

**Grounding climbs to a meaningful AX ancestor.** On legacy markup the hit is usually an inline
wrapper: "Member Inquiry" hits an ignored `<font>` inside the `<a>`. I walk `parentId` past ignored,
`none`, `generic` and `StaticText` nodes to `link "Member Inquiry"`.

**Legacy controls have empty accessible names, which is why `anchor` exists.** The `txtA` input beside
a "Member #" `<td>` is `textbox ""`, and the GO image with no alt is `image ""`. a11y can't address
either, so anchor and css lead for such controls.

## 2026-10-15 — Spike B: screencast and input relay

**Screencast plus a websocket relay works, and coordinates are 1:1.** Frames report
`deviceWidth×deviceHeight` 1280×800, `offsetTop` 0 and `pageScaleFactor` 1, and decode at 1280×800.
A viewer click at canvas (x, y) is dispatched as `Input.dispatchMouseEvent` at (x, y), unscaled.

**Relay rules.** The relay acks every frame (`Page.screencastFrameAck`). It binds and listens before
uvicorn starts, so early connects queue in the backlog with no startup race. The test forces repaints
to obtain frames. Two claims come from the CDP docs and I did **not** test them: frames arrive only on
repaint, and an un-acked stream stalls. The console should show the last frame plus its own heartbeat.

## 2026-10-15 — Spike C: SDK and the computer toolset

**anthropic 1.15.0 types the toolset, so I don't need raw dicts.** `ComputerToolset20260801Param` is in
`ToolUnionParam` and is sent bare with no beta header. Member calls parse as `ToolUseBlock` with a
typed `toolset_name`, and they keep it when `response.content` is echoed back. `ToolResultBlockParam`
has `toolset_name` as well. I dispatch on `(toolset_name, name)`. The per-member classes, such as
`ComputerLeftClickToolUseBlock`, are not in the Messages content union.

**The server accepts the toolset: fully verified.** With a workspace-scoped key, `make test-live`
passed. `claude-sonnet-5-5` with adaptive summarized thinking accepted the bare toolset entry and
replied with a `screenshot` `tool_use` carrying `toolset_name: "computer"`. The first attempt, with
an org key not scoped to a workspace, returned 400 asking for `anthropic-workspace-id`.

**The shared Anthropic client (P6) must honor an optional `ANTHROPIC_WORKSPACE_ID`.** When it is set,
the client sends it as `anthropic-workspace-id` via `default_headers`, as the live test does. Without
this, users whose identity-linked keys are not scoped to a workspace cannot run discovery.

## 2026-10-15 — Demo video exception to invariant 4

**Video is opt-in, mock bank only.** `scripts/evidence/demo_video.sh --record-video` is the only path
that records, and it keeps invariant 7 intact. It records synthetic mock-bank data only to
`evidence/demo.mp4`, under 3 minutes, transcoded from Playwright's webm. Video is never on by default,
and the PII scan can't read it, so the synthetic-only rule is what protects it. Scheduled for P10.

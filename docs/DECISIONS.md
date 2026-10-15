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

## 2026-10-15 — P1 mock bank

**The mock bank is a top-level `mock_bank/` package, outside `src/`.** It is the system under test,
not part of Eagleview, so Eagleview never imports it and reaches it only over HTTP and a browser.
Tests import it through pytest's `pythonpath`; Faker is a dev dependency because only the bank uses it.

**The training password is a secret too.** *(Superseded below: one password per tenant.)* The bank accepts whatever `IRONBRIDGE_TELLER_PASSWORD`
holds; `make bank` refuses to start without it. Convention for P4: `secret://<name>` resolves to the
environment variable `<NAME>` uppercased. Tests generate a random password per session.

**One dataset, seed 7, on both tenants.** *(Superseded below: separate data per tenant.)* The tenants are two credit unions loaded with the same
training data, so scenario 13 can replay member 10482 on both. The training region runs on a frozen
business date (2026-10-15) and DOB is drawn from the seeded RNG, not Faker's today-relative
`date_of_birth`. Member 10482 is pinned to S00, S01 and S10, and a golden test fails if a Faker upgrade
shifts its data.

**Fault semantics.** A route is an exact path, matched for any method. `session_expire_after_requests=N`
counts authenticated page requests only (static images and `/__admin` excluded; a frameset load is 4),
then expires the next requester's session and disarms, so a single relogin can recover. Modal faults fire
on every response for the route and are dismissed client-side. I added `maintenance=on` because the app
profile declares a `maintenance_banner` state that otherwise could never be triggered.

**Inquiry-only operators can look members up.** "ACCESS DENIED" applies to Open Share and Post, as a
200 page with `IBT-0403`, the way legacy apps report it, not an HTTP 403. Lookups need no write rights.

**No double-post guard, on purpose.** The review form carries the transaction in hidden fields with no
submission token, so posting twice opens two shares. Commit safety has to come from Eagleview, which
is the point of the idempotency rule in §4.

**Small realism choices that later phases should know.** Cookie names are per tenant (cookies are
scoped by host, not port). Servers bind 127.0.0.1, not `localhost`. On tenant A, Enter in the search
field does not submit: the form has two text inputs and no submit button, so only the GO image works.
Form bodies are parsed by hand rather than adding `python-multipart`.

**The fake modals block clicks, not the DOM.** Text under the overlay is still in the DOM, and
Playwright still calls it "visible". A step whose `expect` is `text_present` would pass straight
through `unknown_modal_on=/member`. So P3's `text_boxes()` and visibility predicates must hit-test:
text counts as visible only if it is the topmost element at its own centre, through frames.

## 2026-10-15 — P1 revision: tenants share the contract, never data or credentials

**Each tenant has its own members, share types and credentials.** Real credit unions never share
members or logins, and identical data would hide a run routed to the wrong tenant: it would return
a plausible answer. Lantern Bay uses seed 7 and 5-digit numbers (demo member 10482); Copperhill uses
seed 31 and 7-digit numbers (demo member 2210236). A test pins each demo member's name and balances.

**Share types are tenant config.** Each tenant names its products and numbers its suffixes its own
way ("S10 Holiday Club" at Lantern Bay, "20 Christmas Club" at Copperhill). The capability contract
stays shared, so its output schema must not encode one tenant's codes: the suffix pattern is
`^[A-Z0-9]{2,4}$` and the description is whatever label the tenant shows.

**One secret per tenant.** `secret://lantern_bay_teller_password` and
`secret://copperhill_teller_password` resolve to `LANTERN_BAY_TELLER_PASSWORD` and
`COPPERHILL_TELLER_PASSWORD`; the operator IDs differ too. When either variable is missing,
`make bank` generates a random value, appends it to the gitignored `.env`, and names the variable
without printing it. A fresh clone needs no setup.

## 2026-10-15 — P2 schema

**Predicates are single-key mappings, and frames are always paths.** `{text_present: {...}}` is
parsed with a callable discriminator on the one key, so the YAML in PLAN §3 loads as written,
including a bare `output_present: share_accounts` and `not`. A predicate's `frame: main` and a
target's `frame: [nav]` both become a list in memory and on disk, so there is one representation.

**One way to write a target.** Recovery handlers (`dismiss`, `select_then_click`) take a full
`TargetSpec` with ranked locators, not the `{text: "Continue"}` shorthand in PLAN §3.3. The
overlay fixture under `tests/fixtures/` shows the full form.

**`legacy_web` is a web surface.** css and xpath run on `web` and `legacy_web` and not on desktop.
a11y, anchor, text and point run everywhere. PLAN's "web only" means "has a DOM".

**Risk and approval rules are schema rules.** Step risk `read` maps to capability risk
`read_only`, and the declared capability risk must equal the highest step risk. An irreversible
step must carry `requires_approval: true` and `idempotent: false`. A `verified` or `approved`
artifact may not contain an `llm_proposed`, `probed` or `human_demonstrated` outcome.

**The content hash is canonical JSON with provenance excluded and None fields omitted.** Omitting
None (rather than all defaults) means a new optional field leaves old hashes unchanged, but a
changed default still changes the hash, because it changes behaviour. New optional fields must
therefore default to None. Loading a verified or approved artifact whose hash doesn't match fails
(it was edited after review). For a draft, the hash is recomputed.

**YAML goes through the model.** Loading preserves key order, and dumping follows field
declaration order. The round trip drops YAML comments. dump→load→dump is byte-identical, and
number-like strings (`'1.0'`, `'10482'`) stay quoted. The ruamel code lives in
`eagleview.artifacts`, outside the pyright-strict directories.

**`schema_version` is a quoted `MAJOR.MINOR`, and only major 1 is read.** Any other major is
refused before field validation, with `UnsupportedSchemaVersion` naming the file and the supported
major. An unquoted version is rejected, because YAML turns `1.10` into a float.

**Login macros may template credentials, and nothing else.** A capability may use only
`{{ inputs.x }}`. The app profile's login macro may also use `{{ credentials.username }}` and
`{{ credentials.password }}`. A `TenantProfile` supplies those values; its password is always a
`secret://` reference.

**Money is `Decimal`, and a string only in JSON.** Floats are refused at validation. The string
serializer runs in JSON mode only, and it raises "unexpected value" for non-Decimals, so unions
that contain Money still serialize lists and strings correctly.

## 2026-10-15 — P2 policy engine

**Two pure functions, one per enforcement layer.** `check()` (pre-action) and `check_request()`
(network guard) share URL parsing but decide independently. The guard cannot wait for a human,
so an irreversible request without an approval token is blocked, never escalated.

**URLs are normalized before matching.** Paths are percent-decoded until stable, because the server
decodes before routing, which makes `%5F%5Fadmin` `/__admin`. Paths are then slash-collapsed and
dot-resolved, and routes are matched case-insensitively. URLs that contain userinfo are refused.
Origins are per tenant and compared as scheme, host and port, so `localhost` does not count as
`127.0.0.1`, and Lantern Bay never reaches Copperhill.

**Approval tokens are bound to (run_id, step_id).** A token for one step authorizes no other step
and no other run. Operators may act irreversibly; that is allowed but flagged with `audit`.
Operators may never leave the allowlist or reach a denied route.

**Irreversible labels match whole words, only on activating actions.** These are click,
double-click, and Return/Enter/space. "Post" is irreversible; "Posted", "Postal Code" and
"Closed Accounts" are not. Filling a field labelled "Post Date" is not an activation.

## 2026-10-15 — P2 redaction and the evidence writer

**Redaction walks the object tree before serialization.** JSON escaping (`José`) can't hide a
value from it. Dict keys, ints and Decimals are checked as well as strings. Top-level event keys are
redacted too: the property test caught a leak there.

**Amounts are not PII.** Typed masking applies to strings only. Balances pass through, because once
the identifiers next to them are masked they identify no one, and they are the evidence that
typed extraction worked. Array item fields inherit the array's sensitivity unless they declare
their own.

**Value-aware scrubbing.** Every registered value is scrubbed case-insensitively in two passes:
- Pass 1 removes whole values, wherever they appear, along with their HTML- and URL-encoded forms.
- Pass 2 removes name tokens, as words only, so `Ann` does not hit `Annual`. It runs second
  because removing a whole value can leave a glued-on token standing alone.

Values shorter than 3 characters are ignored, and so are name tokens shorter than 3. Secrets are
scrubbed at any length. A resolved `secret://` value is registered the moment it is read.

**Patterns never over-redact ordinary teller text.** DOB and account numbers match only after a
label ("DOB:", "Member #", "Acct Holder No."), so posting dates, transaction dates and transaction
numbers survive. The writer's own structural fields (`ts`, `run_id`, `seq`, `actor`, `kind`) are
never touched. There are two known limits:
- A pattern-only value glued to more digits or letters is not caught, because a regex can't tell it
  from a longer number.
- A label and its value in separate DOM cells don't match a label-anchored pattern.

The mock bank masks SSN and DOB at the source, and value-aware scrubbing covers every value the run
knows.

**Screenshots: whole text boxes are painted, and `text_boxes` is required.** The writer re-encodes
every PNG, which drops metadata chunks, and fills any box the redactor flags with 2 px of padding.
The API offers no way to write an unredacted image. DOM snapshots are scrubbed as whole strings,
so hidden `value=` attributes are covered as well as text nodes.

**Import boundaries are checked statically and at runtime.** The test walks the source import
graph, including ancestor `__init__` files and relative imports, and also imports the guarded
packages in a fresh interpreter. A self-test plants a two-hop `anthropic` import to prove the
checker catches it. A missing guarded package fails the test rather than passing it.

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

**Amounts are not PII.** *(Superseded below: amount masking is a policy switch.)* Typed masking applies to strings only. Balances pass through, because once
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

## 2026-10-15 — P2 follow-ups: amounts, item sensitivity, numbers

**Amount masking is a policy switch, on by default.** `redact_amounts: true` in
`policies/default-teller.yaml` masks amounts in sensitive fields as `[amount]`, both in results and
in text, where `1234.56`, `1,234.56` and `$1,234.56` are matched on digit boundaries, so `0.00`
leaves `$10,000.00` alone. `policies/training-teller.yaml` is identical except for
`redact_amounts: false`, and it is only for synthetic training tenants; evidence scripts use it
so balances show. A test fails if the two policies drift apart in anything else.

**Account-type labels are declared, not exempted.** The capability fixture and the PLAN §3.2
example mark `suffix` and `description` as `sensitivity: none`. Item fields without their own
sensitivity, declared or not, still inherit the array's, so a new field fails closed.

**Numbers are changed only in declared fields.** Value-aware and pattern scrubbing apply to strings
(and dict keys) only. An int, float or Decimal anywhere else is written as it is, so a
`timing_ms` of 10482 survives and a written RunResult reloads. Within a declared field, a numeric
identifier is masked and an amount follows `redact_amounts`.

**Name tokens never rewrite placeholders.** The second scrubbing pass skips `[label]`
placeholders from the first pass. The property test found the case: a name token `NAME` was
turning `[member_name]` into `[member_[member_name]]`.

## 2026-10-15 — P3 web surface

**`act()` grounds at the exact point immediately before acting, and acts there with the mouse.**
The order is: lease check, then the point (a resolved target is scrolled into view first), then
grounding, then the identity check, then the policy, then `mouse.click(x, y)`. I never act through a
selector, because Playwright's `locator.click()` re-resolves, scrolls and retargets on its own, so
the click could land on something that was never grounded. Keyboard actions ground the deep
focus instead, so Enter on a focused "Post" is judged as a click on Post.

**The identity check compares live elements, not paths.** When the caller passes a resolved
target, the element under the point must be that same live node, compared with `===` in its
own frame, or the action returns `target_mismatch` and nothing happens. A table layout can rebuild
an identical DOM path on the next screen; a detached handle or a covering modal fails the check.
`sameTarget` treats a `<font>` inside its `<a>` as the same target as the `<a>`.

**Grounding runs two hit tests and requires them to agree.** CDP `DOM.getNodeForLocation` gives
the node whose accessibility role and name the browser reports. A JS `elementFromPoint` descent
through the frames gives a Playwright handle that resolvers can be compared against. Both climb
to the actionable ancestor, and their DOM keys must match, or grounding retries once and then fails.

**Fill types its value, key by key.** Fill does this:
1. clicks the field, then checks that focus landed on the grounded node (by browser node id);
2. selects the existing text with `el.select()` and presses Delete;
3. types the value with `keyboard.type`, which sends keydown, keypress, input and keyup for each
   character.

`ControlOrMeta+a` was rejected: it is Meta on macOS and Control on Linux, so replay would behave
differently by platform. Values must be printable ASCII. Playwright types any other character
with `insertText`, which fires no key events, so I refuse those values rather than half-type them.
Typing has no `delay`, because a delay is a sleep on the replay path.

**Visible means topmost, through every frame.** Text, controls and table rows count as visible
only if a hit test at their centre lands on them in their own document, and each enclosing
frame element is topmost in its parent at that point. A point outside the viewport is not
visible. `resolve`, `act` and table extraction scroll into view first. `text_boxes()` and
the text predicates report only what is on screen now, matching the screenshot. One known limit:
an overlay with `pointer-events: none` is invisible to hit testing.

**Text boxes include text that controls draw.** A box marked `source: control` covers button
labels, typed field values and the selected option of a select, because none of them is a DOM text
node. Without these boxes, the after-screenshot of `enter_member_id` would be written with the member number
readable, and the `{text: OK}` dismiss handler could not find the OK button. Password values
appear masked, as on screen. Anchors use document text only.

**Ranked resolution takes the first unique locator and never breaks ties.** Ambiguous locators
are skipped, not picked from. The agreement check compares the winner with the next locator that
also resolves uniquely, and it is logged as a `locator.resolve` event. Text and a11y strategies
count only rendered elements. Grounding proposes a11y, anchor (left, right, above), text, css,
xpath and point candidates, and keeps only those that resolve uniquely back to the grounded element.

**Anchor geometry is overlap-based and stays within one frame.** Relations require overlapping
row or column bands, with a 4 px edge tolerance and no absolute positions, because CI fonts
differ. Two candidates within 2 px of each other count as a tie, and a tie is ambiguous. A label
and its control must share a frame. The algorithm reads only `TextBox` geometry, so it lives in
`surfaces/geometry.py` for a future desktop surface.

**The network guard follows redirects itself.** Playwright calls a route handler only for the
first request in a redirect chain, and a fulfilled 3xx is followed unrouted. I checked both
empirically. So the guard fetches every request with redirects off and checks each `Location`.
- For a page or frame navigation, it answers with a stub page that calls `location.replace`, so
  the next hop is a fresh request that comes back through the guard.
- For a subresource, it follows the chain hop by hop.
- A 307 or 308 that would replay a POST navigation elsewhere is blocked.

The policy context is read when each request arrives. Blocked requests are aborted and drained by
the engine. Cookies set during `route.fetch` reach the browser context, so sign-on works.

**The state fingerprint is title plus frame URL paths plus visible headings, with digits
normalized.** This markup has no `h1`, so a heading is `h1`–`h6`, `role=heading`, or bold text of
16 px or more, and it must be topmost. The fingerprint ignores the banner countdown and changes when a
modal covers the page heading.

**A frame that is navigating shows nothing.** Evaluate errors such as "execution context
destroyed" or "frame detached" make that frame empty for this check, so a settle loop polling
during a navigation looks again instead of crashing.

**`act()` checks the holder through a `HolderGuard` protocol.** `FixedHolder` stands in for one
actor at one epoch until P5's SessionController implements the lease. `Action` gained `scroll`
(dx, dy) and `TextBox` gained `source`. Both default safely, and neither is part of an artifact
hash.

**Value parsing is surface-agnostic and lives in `surfaces/extract.py`.** Placing it in
`replay/` would make surfaces import replay, and P4's engine imports surfaces. `money_us`
accepts these legacy negative forms: a leading minus, parentheses, a trailing ledger minus and
a `DR` suffix. `CR` is positive. Two sign markers at once (`-12.34 DR`, `(12.34) CR`) are refused
as contradictory. Table columns are found by header label, tenant aliases included. A row with a
single cell is a table message and is skipped. Any other ragged row is an error.

**Mock-bank fixtures moved to a pytest plugin.** `tests/support/bank.py` and `teller.py` are
shared with the surface tests through `pytest_plugins` in the root conftest.

## 2026-10-15 — P3 review fixes

**One approval token authorizes one irreversible request.** The guard spends a `(run_id,
step_id)` token on the first request it allows, and blocks later ones as `approval_spent`. The
guard sends requests itself through `route.fetch`, so a browser cancelling a navigation no longer
stops a request already handed to the guard. Before this fix, two scripted submits under one
approved binding both posted, and the mock bank has no double-post guard of its own.

**Nothing reads the page between the last check and the mouse.** While discovery records
candidate locators, many page reads run after grounding. `act()` ends with `confirm`: the
grounded element must still be topmost at the grounded point. A failure returns
`target_mismatch`. A spy test checks that the last two calls are always confirm, then click.

**`wait` is refused for every actor except `llm`.** Replay polls predicates and never sleeps
(invariant 9), and the surface enforces this rather than relying on convention. `surface.act`
events name a target, and give its text, only for links and buttons. Otherwise a discovery click on
a name cell would write the name before it is a registered value the redactor scrubs.

**Every network-guard test opens its own browser context and checks at most one top-level
navigation.** A blocked navigation commits Chrome's error page late and can interrupt the next
navigation in the same page. Separate contexts make the tests independent. I confirmed this by
running them in five shuffled orders.

## 2026-10-15 — Mock-bank data: the clock and edge cases

**Seeded data never reads today's date, and a test proves it.** Every date is computed from the
frozen business date, 2026-10-15. The test runs the generator in a fresh interpreter where
`date.today()` and `datetime.now()` say 2031, and requires identical data. I checked that the test
can fail: it does when Faker's today-relative `date_of_birth` is put back.

**Edge-case members are pinned in tenant config and applied after the draws.** Each tenant has:
- an overdrawn share draft (Lantern Bay `-12.34`, Copperhill `-1208.77`);
- a member with loans but no share accounts, whose history is loan payments;
- an apostrophe-and-hyphen surname (`O'Brien-Smith`, `D'Amico-Reyes`).

The overrides use their own per-member random generator, so no other member changes; a test
compares every member with and without them. The demo members stay pinned by the golden test.

**Negative balances print in legacy form, with a trailing minus (`$12.34-`).** Until now no
balance was negative, so nothing else changed. Empty share and activity tables show a single
message row ("No share accounts.", "No recent activity."), which table extraction already skips.
A loans-only member therefore reads as `share_accounts: []`. P4 must decide whether an empty list
satisfies the success predicate `output_present: share_accounts`. As written, it does not.

## 2026-10-15 — P4 replay engine

**Replay works against the Surface protocol and never imports Playwright.** The engine receives a
`SurfaceOpener`. `surfaces/web/launch.py` opens the browser and `eagleview.runner` wires up the
policy, the redactor and the writer, so the CLI and the tests build a run the same way. This lets
the engine rules run in unit tests on a scripted fake surface in about 2 seconds.

**Every wait uses the same priority order, first match wins.** The order is:
1. the app profile's hard states, in profile order;
2. the capability's declared outcomes;
3. interstitials a handler can clear;
4. the step's `expect`.

This order applies while waiting for a target and during sign-on, not only after an action. So a
modal or a maintenance page is reported as itself, never as LOCATOR_NOT_FOUND. A target that is
already uncovered and unique is used at once. That is what lets a retried click on the nav link go
ahead while the error page is still showing in another frame.

**The 150 ms poll is the only sleep, and a test enforces it.** `settle.pause()` is the one sleep in
`eagleview.replay`. An AST test fails on any other `sleep` or `wait_for_timeout` in the package.
There is no settle delay after an action: the loop moves on the moment a predicate holds.

**A true-before `expect` needs a visible change.** Each step's `expect` is checked before it acts.
If it already holds, the step passes only once a document has loaded in some frame (a navigation
counter, because reloading the same page leaves the fingerprint unchanged) or the state
fingerprint moved. Fill and select are exempt: they change no document or fingerprint, and their
own result proves the value was set. Without the guard, the stale-expect drill's no-op click passes;
I checked that the test then fails.

**A commit is a sent irreversible action, and it changes every result after it.** An irreversible
step counts as committed when `act()` returns `done` or `error`, because a browser error can arrive
after the click was dispatched. From then on, every non-success result becomes OUTCOME_UNCERTAIN.
That covers timeouts, app errors, unknown screens, session expiry, invalid output, business outcomes
and the run deadline. These results are never retryable, and sign-on never runs again. Running out of
sign-ons before any commit is SESSION_LOST; the app profile now says so instead of
OUTCOME_UNCERTAIN.

**Retry means running the same step again.** `retry_step_if_idempotent` resolves and acts again.
If the error page replaced the step's target, waiting for that target finds the error page still
showing with its budget spent, and the run fails APP_ERROR. The fault test arms `error500_on=/inq`,
the GET that the first step's click opens. A missing budget (`max_per_run`) means one use.

**An empty list is an answer.** `output_present` now means extracted and not null. A loans-only
member returns `share_accounts: []` with status success; a missing table still fails extraction.

**Unattended is an explicit flag.** `RunOptions.unattended` (`--unattended`) needs an `approved`
capability. It never approves its own irreversible steps, so combining it with `--approve-gates`
is refused, and such a run fails POLICY_BLOCKED at the gate. It also refuses `--allow-modified`.
Attended runs may use drafts, because P7's verify step replays them. `Rejection` gained an optional
`message` that says why.

**`--approve-gates` grants one token per gated step, just before that step.** The token is bound to
(run_id, step_id), and the network guard spends it on the first irreversible request. Each grant is
recorded as an `approval` intervention. Without the flag, the gated step fails POLICY_BLOCKED before
it acts.

**`needs_human` carries the failure that parked it.** In P4, `--escalate` turns a hard failure into
`needs_human` with the failure attached and a `takeover` intervention marked as parked. P5 makes the
escalation block on a human.

**The run deadline wraps the whole run.** `asyncio.timeout(deadline_s)` covers the version check,
sign-on and every step; the default is 120 s. Passing it gives STATE_TIMEOUT, with a message naming
the deadline and the step reached, and OUTCOME_UNCERTAIN after a commit. Evidence is still captured
after the cancellation.

**Screenshots are written at the end of the run.** They stay in memory until every extracted value
is registered with the redactor, so the screenshot taken after the search is painted over the
member's name. **Known limit:** sensitive text on screen that the run never reads or types, such as
the address on Member Detail, is caught only by the pattern layer. A per-screen redaction list in the
app profile would close this; I've left it for later.

**The app fingerprint is `<profile>@<major.minor>/<tenant>`.** For example,
`ironbridge-teller@7.2/lantern-bay`. It is read from the sign-on page footer before signing on. The
lookup fixture's point locator and the P3 test now use this form.

**Committed configuration.** `app_profiles/ironbridge-teller.yaml` moved out of the test fixtures,
because app profiles are authored, not discovered. Its username selector is fixed (`f1`), and a
test resolves every login and dismiss locator, one at a time, on both tenants. `tenants/` holds
both tenant profiles. `python-dotenv` is now a runtime dependency: the CLI reads `.env`, where
`make bank` writes the passwords, and variables set in the shell win.

**The sign-on hands off through a `HandoffHolder`.** The harness holds the session during sign-on
and automation holds it for the steps. Each hand-off bumps the epoch, so a stale caller is refused.
P5's SessionController replaces it. Harness fills no longer log `text_length`, so not even a
password's length reaches disk.

**CLI contract.** The result JSON goes to stdout, unredacted, because the caller asked for those
values. A summary goes to stderr. Everything on disk goes through the writer. The exit codes are 0
success, 3 outcome, 4 rejected, 5 failed and 6 needs_human; 2 is left to usage errors.

**Until P8, Copperhill stops at Select Branch.** Its handler lives in the tenant overlay, and
choosing a branch by position needs the overlay's surface support. A test pins the current
behaviour: UNKNOWN_STATE at sign-on, observed on `/branch`.

## 2026-10-16 — Screen-level redaction (before P5)

**This closes the P4 known limit, before any discovery trace or evidence is written.** Until now,
personal data on screen that a run never read or typed was caught only by the pattern layer, such as
the address and notes on Member Detail. It was written readable in screenshots and failure DOM
snapshots under `runs/`, which broke invariant 4.

**[REVIEW] The app profile lists sensitive labels per screen.** `sensitive_labels` is a list of
`{screen, labels}`. `screen` is text that identifies the screen, its heading here, compared as a
whole label; omitting it applies the rule to every screen. Ironbridge lists:
- Member Search: Member #, Last Name.
- Member Detail: Name, Member #, SSN, DOB, Phone, Email, Address, Notes.
- The Member line on Open Share, Review and Posted.

The alternative was one global label list, which is simpler. I rejected it because a label such as
"Member" means a person on one screen and a menu heading on another. Not listed: Member Since
dates, loan balances and transaction amounts, which identify no one once the identifiers are masked.

**[REVIEW] What counts as "the value beside a label" is geometry, not markup.** The value region
starts at the label's right edge. It runs across to the next label-like text on that row (a listed
label, or text ending in a colon). It runs down to the next text in the label's own column, which
covers a two-line address, and never more than four label heights. The rule reads only text boxes,
so a desktop surface can reuse it, like the anchor strategy. The alternative, the next table cell
in the DOM, is more exact on this app. I rejected it because it would not carry over to OCR or UI
Automation.

**The writer registers those values; it does not paint rectangles of its own.** Before each
screenshot, `EvidenceWriter.register_screen` registers every labelled value as personal data.
Value-aware redaction then paints those boxes and scrubs them from every later DOM snapshot and
event. The engine already writes screenshots before DOM snapshots, so a failure snapshot is
scrubbed of everything its screenshot showed.

**Redaction now sees covered and out-of-view text.** `text_boxes(every=True)` includes text under a
fake modal's translucent overlay, which a person can still read in the screenshot, and text
scrolled out of view, which a DOM snapshot contains. Locating still uses only what is visible.
Before this, value-aware painting also skipped covered text, so a modal could leave a registered
value readable. A test fills the Member Detail screen with and without the unknown modal and
checks every personal value's box is solid black and absent from the DOM snapshot. Another test
shows that a failure run's evidence no longer contains them, and it fails when the engine stops
passing the labels to the writer.

## 2026-10-16 — P5 plan (self-reviewed with the advisor, not with the user)

**What gets built.**
- `control/session_controller.py`: the lease (holder plus fencing epoch) and the state machine
  from PLAN §5.
- `control/interventions.py`: an SQLite store under `runs/`, plus in-process waiters.
- `operator/`: a FastAPI console on port 8700 with an interventions list, a session page with a
  live screencast canvas, and a JSON API (take, act, resume, approve, deny, abort, state, boxes).
  The page and the scripted test operator use the same API.
- Replay integration: blocking escalation, approval interventions, operator preemption, resume
  validation, recording of human actions, and a proposed `human_demonstrated` overlay.

**[REVIEW] Without a console, P4 behaviour is unchanged.** Interventions block only when a run has
an intervention desk attached. The CLI attaches one, and starts the console on 8700, only when a
human could be needed: `--escalate`, or a gated step without `--approve-gates`. Without a desk,
`--escalate` parks the run as `needs_human` and a gated step fails POLICY_BLOCKED, as in P4. The
P4 tests stay as they are.
The alternative was to start a console for every run. I rejected it because a run nobody can answer
would then block instead of failing at once, and P4's tests pin that behaviour.

**[REVIEW] Waiting for a human does not count against the run deadline.** The deadline bounds what
automation spends, so it is pushed back by however long a human wait took. A human wait has its own
limit (`--human-timeout`, default 15 minutes). If nobody answers in time, the run returns
`needs_human` with the failure attached; nothing is posted. The alternative, one deadline covering
human time too, would make any real takeover fail at 120 s.

**[REVIEW] An operator can take control in two ways.**
- **Pause** is cooperative: automation finishes its current step and yields at the step boundary.
- **Force take** moves the lease at once and bumps the epoch, so automation's next `act()` is
  refused as `not_holder`.

The alternative was force take only. I rejected it because cutting in mid-step can leave a half-
typed field, while pause hands over at a clean boundary.

With a desk attached, `not_holder` now means "preempted": the engine waits for the human to hand
back. Without a desk it is still POLICY_BLOCKED.

**[REVIEW] Unattended runs ask a human through the console.** An unattended run never approves its
own irreversible step. With a desk attached, it opens an approval intervention and blocks until a
human approves, denies or times out. `--approve-gates` is still refused with `--unattended`. Deny
gives POLICY_BLOCKED, and a timeout gives `needs_human`; neither posts.
The alternative was to refuse every gated step in unattended mode, as in P4. I rejected it because a
person approving in the console is exactly the approval the gate asks for, and the run still never
approves itself.

**The commit rule covers operator actions.** Operator input goes through `Surface.act` as
`operator:<name>`, at the controller's current epoch. When an operator's action is irreversible and
done (the policy allows it, audited), the run counts as committed. From then on every non-success
is OUTCOME_UNCERTAIN, and resume never acts on that step again.

**[REVIEW] Resume validation.** PLAN §5 is followed first:
1. If a hard global state shows, the result is HANDOFF_STATE_MISMATCH.
2. Otherwise, take the latest step, from the paused one onwards, whose `expect` holds, and resume
   after it.

The alternative was PLAN §5 alone. I rejected it because a person who only closes a covering modal
would always get HANDOFF_STATE_MISMATCH, although re-running a step that was never sent is safe.

One extension: if no `expect` holds, the paused step's target resolves uniquely again, and that
step was never sent (or is idempotent and nothing is committed), the paused step runs again. This
covers a human closing a modal that was covering the target. Anything else is
HANDOFF_STATE_MISMATCH.

**[REVIEW] Human-demonstrated patch.** After a takeover the human resumes from, the operator's
clicks, each grounded into candidate locators as discovery does, are written to
`runs/<run_id>/proposed_overlay.yaml` through the writer, redacted. The patch is a draft tenant
overlay with `source: human_demonstrated`. It adds a global state:
- detect: the first new text shown at the moment of escalation;
- recover: a new `click_sequence` handler.

The alternative was to apply the person's fix automatically. I rejected it because an unreviewed
patch would change replay behaviour without anyone signing off.

Replay runs that handler type, and an engine test proves it. The patch is never applied
automatically, and it can be replay-verified only once P8 resolves overlays.

**Not built in P5:** authentication for the console, webhooks, and parked asynchronous runs (all
in REPORT §7). Raw screencast frames go only to the operator's browser and never touch disk.

## 2026-10-16 — P5 as built

**[REVIEW] Intervention notices go to stderr, not stdout.** PLAN §5 says "a stdout line", but
`eagleview replay` reserves stdout for the result JSON a caller parses. The line names the run, the
reason and the console URL.
The alternative, the PLAN's stdout line, was rejected because it would break every caller that
parses the result JSON.

**[REVIEW] The console exposes what the operator sees as text: `GET /api/sessions/{run}/boxes`.** It
returns the visible text with geometry, raw, over loopback, like the screencast. The scripted
operator needs it to find what to click; a person uses the picture. Operators are trusted
(PLAN §6), and nothing from it is written to disk.
The alternative was OCR of the screencast for the scripted operator. I rejected it because it adds a
dependency and flakiness to evidence that should be deterministic.

**[REVIEW] At most three takeovers per run.** Each needs a human. Without a cap, a screen that keeps
failing would keep asking. After the third, a failure is final.
The alternative was no cap. I rejected it because a screen that fails every time would keep asking a
person; a different cap is a one-line change.

**The layer rule for labelled values.** With covered text included, a modal's own text sits
geometrically beside the labels beneath it. The first console test showed it: the drawer alert's
words were being registered as the member's name. Text boxes now say whether they are `covered`,
and a label's values come only from its own layer.

**A proposed patch is redacted as data, then dumped.** Scrubbing the YAML text inserted `[name]`
placeholders that broke the YAML syntax. The writer now redacts the data tree and then serializes
it, so a placeholder is always a quoted string.

**[REVIEW] What the human saw that automation did not.** The patch's `detect` is the first text
visible at the takeover that was not visible before the paused step. Text that differs only in
its digits doesn't count, which rules out the banner's session countdown, and the paused step's
own frame is searched first. For the drawer alert that gives "Cash Drawer Alert". A reviewer
checks it before the overlay is ever used (P8).
The alternative was asking the operator to type the trigger. I rejected it because it adds a form
during an incident, and the reviewer checks the guess anyway.

**P4 behaviour holds without a desk.** All P4 replay tests pass unedited: `--escalate` without a
console still parks, and a gated step without `--approve-gates` still fails POLICY_BLOCKED.
`HandoffHolder` is gone; the SessionController's `hand_to` does that job.

## 2026-10-16 — P6 plan (self-reviewed with the advisor)

**Order.**
1. Build the loop: tools, prompts, recorder, sign-on reuse and the spend ledger.
2. Test it offline with a scripted fake model.
3. Record **one real trace** for the PLAN §0 goal and write `docs/DISCOVERY_TRACE_REVIEW.md` from it.
4. Adjust the loop from the review, then commit.

No compiler code before the review is committed.

**The spend limit is enforced on every real request.** A ledger at `runs/discovery_ledger.jsonl`
(gitignored) gets one line per real API response, written as the response arrives, so a crash
still records the spend. Before every real request (input extraction, probes, each loop turn)
the ledger total must stay under $25. A run counts toward the 30-run cap as soon as it makes its
first real call. Crossing either limit stops discovery cleanly, and docs/STATUS.md says where it
stopped. The meter is injected, so fake-transport tests never touch the real ledger.

Each turn is priced from its usage:

| Usage | Sonnet 5.5 | Opus 5.5 |
|---|---|---|
| Input | $2.00 | $4.00 |
| Cache writes (5-minute) | $2.50 | $5.00 |
| Cache reads | $0.20 | $0.20 |
| Output | $10.00 | $20.00 |

Dollars per million tokens; thinking is billed as output.

**[REVIEW] Model and effort.** The model is `EAGLEVIEW_MODEL` or `--model`, default
`claude-sonnet-5-5` (CLAUDE.md). Effort is set explicitly to `medium`, the starting point the
API guidance gives for multi-step tool use after Sonnet 5.5 recalibrated its levels. Leaving the
default (`high`) was the alternative; it costs more on a 40-action loop. Both values are recorded
in provenance.

**[REVIEW] Refusal fallbacks are on: `fallbacks: "default"`.** The API guidance recommends this
for Sonnet 5.5 on the Claude API, so a policy decline is retried on another model. If a fallback
serves a turn, the turn is priced at the dearer of the two models, and every serving model is
recorded. If the API rejects fallbacks alongside the computer toolset, that call is still
ledgered; fallbacks are switched off and recorded here, with no retry loop.
The alternative was fallbacks off. I rejected it because one policy decline would then end a
discovery run that another model could finish, and the price is recorded per turn.

**[REVIEW] Toolset members the policy denies are disabled.** These are:
- `right_click`, `middle_click` and `left_click_drag`;
- `left_mouse_down` and `left_mouse_up`;
- `hold_key` and `cursor_position`.

The alternative was offering the whole toolset and letting the policy refuse. I rejected it because
each refused attempt costs a turn and teaches the model nothing.

The model cannot try what would only be refused. `zoom`, `wait` and `scroll` stay.

**[REVIEW] What counts toward the limits.** The 40-action cap and the "same fingerprint after 4
actions" rule count only actions that can change the screen: clicks, type, key, scroll,
mouse_move and `fill_input`. Screenshot, zoom and wait only look. A separate cap of 80 tool calls
of any kind bounds the cost of a model that only looks.
The alternative was counting every tool call toward the 40-action cap. I rejected it because a model
that zooms to read small text would run out of actions without doing anything.

**Credentials.** The API key comes from the merged environment (the shell over `.env`), is passed
to the client explicitly, is registered with the redactor as a secret, and is never logged. The
optional `ANTHROPIC_WORKSPACE_ID` is sent as a header (Spike C).

**Policy mode is `discovery`.** An irreversible action returns `escalate`. With a desk attached it
becomes an approval intervention (P5). Without one, it is an `is_error` result and counts toward
the stop rule of 2 policy blocks.

**Tool results follow the toolset contract exactly.**
- Computer members' results echo `toolset_name: "computer"`; results of custom tools never do.
- Each turn's results go back in one user message, in order.
- After a failure, the remaining computer members get the exact halt text and the remaining
  custom tools get `is_error`.
- If the batch didn't end with a screenshot, a fresh one is attached to the last result.

History is append-only: `response.content` is appended verbatim, and screenshots are never pruned.

**Sign-on is shared, one way.** Replay exposes `open_session()`: open the surface, check the
version, and sign on as the harness. Discovery imports it; replay never imports discovery. The LLM
gets the session only after sign-on, as the actor `llm`.

**Not doing:** server-side context editing. 40 actions with screenshots fit easily in the 1M
window, and CLAUDE.md asks for it only if context grows.

## 2026-10-16 — P6 as built, after the real trace

The review is in `docs/DISCOVERY_TRACE_REVIEW.md`. Two real runs cost $0.08 in total, and both
succeeded in 3–5 turns.

**Screenshots are painted with boxes read on both sides of them.** The first real run caught an
after-screenshot taken mid-load, whose paint boxes came from the loaded page. `surfaces/capture.py`
reads text boxes before and after each screenshot and merges them, in replay and discovery alike.
Painting slightly too much is the safe direction.

**[REVIEW] Discovery lets the screen settle before recording an after-state.** It waits until the
fingerprint holds twice, or 3 s pass. This sleep happens in authoring only; replay still waits on
predicates alone. The alternative, recording immediately, left the compiler looking at
half-loaded pages.

**[REVIEW] One sentence of rationale per batch, by prompt.** At effort `medium` the model returned
neither thinking nor text, so the trace had no rationale. The system prompt now asks for one short
sentence before each batch. Raising the effort to get summarized thinking was the alternative; it
costs more on every turn, and the sentence is what a reviewer actually reads.

**[REVIEW] A marked output is registered with the redactor the moment it is marked.** The model
quoted the balances it had marked in its `declare_success` description, and they reached the trace
readable. Marked values are now registered as personal data, and a marked table's amounts go
through the amount rule, so they follow `redact_amounts`.
The alternative was registering marked values at the end of the run. I rejected it because anything
the model wrote in between, its success description included, would reach disk readable.

**Drafts never overwrite.** `write_draft` creates `capabilities/drafts/<id>.yaml` exclusively,
and adds `.2`, `.3`, … when the name is taken. P7's compiler writes through it.

**The intervention store accepts a discovery goal.** `request_human` during discovery opens a
takeover naming the goal rather than a capability, and it waits at most `human_timeout_s`.

## 2026-10-16 — P7 plan (self-reviewed with the advisor)

**What gets built.** A `compiler/` package:
- **segment:** trace to steps;
- **prune:** shortest path over state fingerprints;
- **derive:** expects, success, outputs, input patterns;
- **name:** Claude, structured output;
- **assemble:** a Pydantic `Capability`;
- **verify** and **probe:** replays;
- **pipeline:** discover, compile, verify, probe, `write_draft`.

Plus the CLI: `eagleview discover` runs the whole pipeline, and `eagleview approve` exists but is
never run by me.

Scenarios 02, 03 and 11 need write flows, so the compiler handles fill, select, click, press and
irreversible steps, not only the lookup.

**The recorder learns what the compiler needs, so new traces are recorded.** For each
screen-changing action the trace gains:
- the text that appeared (visible after, not before), with its frame and whether it is a heading;
- for selects, the chosen option's label, read after the action.

Traces from P6 lack these, so the P7 pipeline re-runs discovery, and those runs count against the
budget.

**[REVIEW] Discovery can get an approval.** *(Superseded by the correction in "P7 as built": discovery never commits; a person performs the irreversible action.)* An irreversible action in discovery returns
`escalate`. With a desk attached, discovery opens an approval intervention, and the scripted
operator answers it in evidence scenario 03. On approval it grants a token bound to (run, action)
and acts again. Without a desk, the action is refused as in P6.

This needs one policy change: an approval token for the step now allows the action in discovery
mode too. Before, discovery always escalated, so nothing could ever be approved. Every actor except
an operator still needs the token, and only a person resolves the intervention that grants it.

**Segmenting.** A click on a field followed by `fill_input` or a bound `type` becomes one `fill`
step on the clicked target. A click on a select followed by keys or typing becomes a `select`
step with the recorded option label. Return or Enter becomes `press`. Screenshot, zoom, wait,
scroll and mouse moves are dropped.

**[REVIEW] Pruning.** Screen-changing steps are pruned by the shortest path over the fingerprint
graph, from the first state to the success state. Value-setting steps (fill, select) don't change
the fingerprint, so they are kept when their state is on that path, and only the last one per
field. Pruning them by the graph was rejected: every fill is a self-loop and would be lost.

**Expects.** A click's `expect` is text that appeared after it: a heading first, otherwise the
first new text in the frame that changed. Text containing a redaction placeholder or only digits
is skipped. When the next step has a target, `element_present` of that target is added under
`all`. A fill or select expects its own value (`field_value: self`).

**[REVIEW] Outputs fail closed.**
- A marked value becomes a string output, or a decimal when it parses as money.
- A marked table becomes an array: one item field per non-empty header, decimal where every cell
  parses as money.
- Sensitivity is `pii` for every output, and item fields inherit it.

The alternative was guessing sensitivity from the field name. I rejected it because a wrong guess
leaks data, while a cautious default costs a reviewer one edit.

The fixture's `sensitivity: none` for suffix and description was a reviewer's choice, and a
reviewer can make it again.

**[REVIEW] What the naming pass may change.** It proposes:
- the capability id, title and description;
- step ids and intents;
- output and item field names;
- input descriptions, examples and patterns;
- raising a step's risk from `read` to `write_staged`.

The alternative was letting the naming pass edit anything. I rejected it because locators,
predicates and risk are what replay safety rests on, and an LLM must not lower them.

It may not touch locators, predicates or order, and it may never lower a risk. An irreversible
risk comes only from the policy decision recorded in the trace. A proposed input pattern must
match the discovery value. The prompt asks for the shape of the value, not this tenant's length,
so the same contract works at a tenant with longer member numbers (P8).

**Verify and probe.**
- **Verify:** replay the draft with the discovery inputs, normalized as P4 does. Success makes it
  `verified`, with the run recorded in provenance.
- **Probe:** Claude proposes 3 inputs that are valid in shape but unlikely to exist. Each runs in
  probe mode: on a terminal UNKNOWN_STATE or STATE_TIMEOUT, the engine writes the visible text,
  redacted, to `probe.json`.
- **Classify:** Claude turns each distinct state into an outcome code, a message and a regex. The
  regex must compile and match the captured text, or it is rejected.
- **Confirm:** the probe runs again with the outcome added. If it now returns that outcome, its
  source becomes `probed_verified`.
- **Final verify:** the success replay runs again with the outcomes in place, so no outcome fires
  falsely.

Artifacts stay `verified`. I never approve.

**P7 plan addendum, from the advisor's review.**
- **[REVIEW] Verifying an irreversible capability needs a person.** The verify replay of a post flow
  runs with a desk attached, and a person approves the post in the console. In evidence that is
  the scripted operator, labelled as such, on the synthetic training tenant after a bank reset.
  Verify never passes `--approve-gates` behind anyone's back. **Probe replays never get a desk or
  an approval**, so a "nonexistent" member that does exist stops at the gate; a test proves it.
  The alternative, verifying with `--approve-gates`, was rejected because it would post with no
  person involved.
- **Checkbox and radio clicks set values.** They count as value-setting steps and survive
  pruning. A graph edge is the recorded `changed` flag (a new fingerprint or a new document), not
  the fingerprint alone.
- **Values bind to inputs by equality, for selects too.** An option label equal to an input value
  compiles to `{{ inputs.<name> }}`.
- **Probe regexes are checked hard.** A regex containing a redaction placeholder or the probe value
  is rejected. Probe inputs must pass the input schema before they run. An outcome whose confirm
  run fails is dropped, never left as `probed`.
- **Every compiler call (naming, probe proposal, classification) is metered under the discovery run's
  id**, so one pipeline counts as one run.
- **Predicates never depend on inputs.** Expects and the success predicate skip any text with a
  placeholder, and a marked value's text never becomes a predicate.
- `eagleview discover` starts the console when an approval may be needed, so scenario 03 runs from
  the CLI.

**[REVIEW] Correction to the P7 plan: discovery never commits, a person does.** I had planned to let
an approval token allow an irreversible action in discovery mode. A P2 policy test pins the
opposite: discovery never commits, even with a token. Changing that expectation would weaken a
safety test, so the policy is unchanged.

Instead, when the model reaches an irreversible action, discovery opens a takeover with reason
`APPROVAL_REQUIRED` and hands the session to a person. The person performs the action in the
console; an operator may act irreversibly, audited (PLAN §5). Their clicks are grounded and
recorded in the trace as `operator:<name>` steps, the same way the model's are, and the model is
told a person did it. The compiler turns that recorded irreversible click into an irreversible
step with `requires_approval`. Replay then needs an approval for it, as before.

## 2026-10-16 — P7 as built

**The first real probe proposal was declined before any output.** It billed 528 input tokens
and returned nothing. The prompt asked for values "unlikely to exist in the institution's
records", which reads like probing a real bank.
- The prompt now says plainly that this is testing on a synthetic training tenant, and why the
  values are wanted.
- Every structured call checks the stop reason and raises a typed `StructuredRefused` instead
  of crashing.
- **[REVIEW]** If Claude still proposes nothing usable, the pipeline falls back to deterministic
  probes of the same shape: all nines, then all zeros, kept only if the input schema accepts them.
  The alternative was to stop with no outcome probed. I rejected it because MEMBER_NOT_FOUND is the
  outcome every lookup needs, and a fallback probe must still pass the same confirm run.

The second real pipeline succeeded: verified, with MEMBER_NOT_FOUND `probed_verified`, for $0.04.

**[REVIEW] A select bound to an input chooses that input's option by provenance.** In Chromium,
typing into an open list only highlights an option; the choice commits when the list closes. So
the value read just after typing can be stale. When the model types a declared input into a
select, the option binds to that input, as a fill does.
The alternative, trusting the value read after typing, was rejected because in a real run it was the
stale "-- Select --".

**A key that changes the screen is a step, even while a select is being worked.** Enter on this
app's select submits the form. Folding that key into the select had broken the shortest path.
The prompt no longer suggests pressing Enter to confirm a choice.

**Probe regexes are checked semantically too.** A regex that escaped the brackets around a
placeholder (`\[member_id\]`) passed the first check. Escapes are now stripped before the
placeholder check, and the regex must still match once every placeholder is replaced by another
value.

**[REVIEW] The final verify runs only when probing added outcomes.** Without new outcomes the first
verify stands, so an irreversible flow is not posted a third time just to re-prove itself.
The alternative was always verifying again after probing. I rejected it because, for a post flow,
that posts again and proves nothing new.

**The import boundary is stricter.** `eagleview.control` joins the guarded packages, and
`eagleview.compiler` joins the forbidden imports, beside `anthropic` and `eagleview.discovery`.

**Real spend so far: $0.16 over 4 runs.**

## 2026-10-16 — P8 + P9 plan (self-reviewed with the advisor)

**The committed lookup capability comes from discovery.**
`capabilities/drafts/member.lookup_share_balances.yaml` is the verified draft produced by the real
P7 pipeline run, copied unchanged and still `verified`. Replay evidence and the Copperhill overlay
are written against it. Approving it, which promotes it to `capabilities/`, is the user's step.

**Overlay resolution (`eagleview/overlays/`).**
- **Order:** base capability, then version patches, then the tenant overlay.
- **Step patches** are keyed by the capability's semantic step ids. They can prepend, append or
  replace locators, and replace an expect or a timeout. A patch naming a step the capability
  doesn't have is an error, not a silent no-op.
- **Global states** from the overlay are added after the app profile's own.
- **Label aliases** reach the surface (anchor, text and a11y strategies) and the evidence writer
  (sensitive labels).
- **The effective hash** covers the patched capability and every overlay applied, so a run
  records exactly what governed it.

**[REVIEW] Version patches use a version the tenant declares.** `TenantProfile.app_version`
(`"7.2"`, `"7.4"`) selects which version patches apply before the run, and replay fails
APP_VERSION_UNSUPPORTED if the screen shows a different version. The alternative, re-resolving
after the footer is read mid-run, would change the artifact during a run.

**[REVIEW] Copperhill's Select Branch handler chooses a named branch.** It uses `option:
"Hillcrest"`, not `option_index: 0`. The overlay is tenant-specific anyway, a label is
reviewable, and choosing by position would need a surface call to list options. That call is not
built (`option_index` still reports "unsupported").
So the alternative, choosing the first option by position, was rejected.

**The Copperhill overlay is hand-authored and reviewed like any artifact.** It holds:
- label aliases ("Member #" is "Acct Holder No.", "Member Inquiry" is "Member Lookup");
- the Select Branch interstitial;
- one step patch: the search step gets a "Search" button locator, since Copperhill has no GO
  image.

**Drift telemetry.** `eagleview telemetry <runs dir>` aggregates every run summary's locator report
per capability, tenant and step: how often each (strategy, rank) won, and the agreement rate.
When rank 0 stops winning, the screen has drifted.

**Agent catalog (P9).**
- **[REVIEW]** The registry lists only `approved` capabilities from `capabilities/`. Irreversible
  ones are left out, because an agent's run is unattended and could only block at the gate.
  The alternative was listing irreversible capabilities too. I rejected it because an agent can't
  answer an approval, so such a tool could never succeed.
- Each capability maps to a tool: a snake_case name, a description built from the title,
  description, outcomes and risk plus the output shape, and the input JSON Schema.
- `eagleview mcp` serves the tools over stdio with the MCP SDK's low-level server (see below). Each call runs an unattended replay
  on the configured tenant and returns the RunResult JSON.
- `scripts/agent_demo.py` has Claude, with plain tool use, pick the tool and answer.

No capability is approved yet, so scenario 14 is built and tested offline (with a test-only
approved copy) and marked pending the user's approval.

**P8 + P9 plan addendum, from the advisor's review.**
- **[REVIEW] Copperhill gets two overlays.** One is profile-wide (`capabilities: ["*"]`): label
  aliases and the Select Branch interstitial. The other is capability-scoped
  (`capabilities: [member.lookup_share_balances]`): the search step patch. A step patch naming a
  missing step is an error only when the overlay names that capability explicitly. Under a glob it
  is skipped, so the profile-wide overlay works for every capability.
  The alternative was one overlay holding everything. I rejected it because the search patch names a
  step of one capability, and would fail every other capability at Copperhill.
- **Overlays are opt-in.** The CLI and callers load them; `run_replay` never does by default. With
  no overlay, the effective hash is the content hash, and the P4 "base fails at Copperhill" test
  stays as it is.
- **The Select Branch handler has `max_per_run: 3`**, so a relogin after session expiry can pass
  Select Branch again. Session expiry on Copperhill is tested.
- **Aliases reach the resolver, the evidence writer and the watcher**, and the overlay's states join
  the watcher's. A Copperhill run's files are scanned for the member number and name.
- **The proof checks how the step succeeded**, not only that it did. At Copperhill the anchor "link
  below FUNCTIONS" would find Cash Drawer, so the test asserts the menu step won by `a11y` at
  rank 0. The search patch uses an `a11y` "Search" button.
- **MCP owns stdout during a call.** Nothing else prints there. Tests use the SDK's in-memory
  client, plus one stdio smoke test.
- **The agent demo is metered** through the same ledger, uses `tool_choice: auto`, is tested
  offline, and is not run for real until a capability is approved.

## 2026-10-16 — P8 + P9 as built

**The committed lookup capability came from the real P7 pipeline.** It was copied byte for byte
to `capabilities/drafts/member.lookup_share_balances.yaml`, and its hash checks out. Its step
ids (`submit_member_search`) are the ones the Copperhill overlay patches.

**Copperhill's sign-on makes 6 authenticated requests, not 4.** It adds GET and POST `/branch`
before the frameset. The session-expiry test arms the fault at 6 and shows the relogin passing
Select Branch a second time, within the handler's budget of 3.

**The proof checks how each step succeeded.** At Copperhill the menu step won by `a11y` at rank 0,
through the alias "Member Lookup", and the search step by the patched `a11y` "Search" button.
Copperhill's run files contain neither its member number nor the member's name.

**[REVIEW] MCP is built on the SDK's low-level `Server`.** MCP 2 renamed FastMCP to `MCPServer`,
which derives each tool's schema from a Python signature. Catalog tools take their JSON Schema,
patterns included, from each capability, and the low-level server accepts that schema as given.
The tests use the SDK's in-memory client, plus one stdio round trip that proves stdout carries
only the protocol.
The alternative, `MCPServer` with generated wrapper functions, was rejected because the generated
schema drops each input's pattern, the contract's first check.

**The agent demo exists and is tested offline only.** `scripts/agent_demo.py` refuses to run
until a capability is approved, and its model calls go to the same spend ledger.

## 2026-10-16 — P10 + P11 plan (self-reviewed with the advisor)

**One Python evidence driver, thin shell scripts.** `scripts/evidence/NN_name.sh` (one per §8
scenario) each call `python -m scripts.evidence.driver NN`. `make evidence` runs them all in
order after deleting `evidence/`. The driver:
1. starts both tenants on their real ports, resets the bank and clears faults before every
   scenario;
2. uses the committed artifacts and tenant files, and the training policy (synthetic data, so
   balances stay readable);
3. copies each run directory, already redacted by the writer, into `evidence/NN_name/`;
4. writes `result.json` and a static `index.html` timeline for each run.

`evidence/README.md` is generated by `scripts/evidence/index.sh` from the scenario results. No file
under `evidence/` is written by hand.

**[REVIEW] Replay scenarios use committed artifacts; discovery scenarios make their own.**
- Scenarios 04–11, 13 and 15 replay `capabilities/drafts/*.yaml`, so they need no API key and run
  identically each time.
- Scenarios 01, 02, 03 and 12a call the real model and keep their compiled artifact copy inside
  their evidence folder. They never write into `capabilities/`.
- The post flow that scenario 11 replays is committed the same way as the lookup: a verified
  draft from a real discovery, copied unchanged.

The alternative was discovering afresh for every replay scenario. I rejected it because those
scenarios would then cost money, need a key and vary from run to run.

**Scenarios that need a person use the scripted operator, and say so.** That covers 03 (Post
during discovery, then the verify approval) and 10 (the unknown-modal takeover). The operator
uses only the console's HTTP API. Every artifact it touches is labelled
"SCRIPTED OPERATOR (stands in for a person)".

**Scenario 12, the prompt-injection drill, has two parts.**
- **12a:** a real discovery on the member whose notes carry the injected instruction, recording
  whether the model ignores it.
- **12b:** a scripted model that does comply, so the evidence shows the policy and the network
  guard blocking `/__admin/faults`.

**Scenario 14 (agent catalog) is pending the user's approval.** With no approved capability, its
script writes a `PENDING.md` that says exactly what to approve. After the user approves,
`make evidence` runs it for real.

**Demos.**
- `make demo-offline`: starts the bank, replays the committed artifacts through the main
  scenarios, drives the takeover and the approval with the scripted operator, needs no API key
  and no human, and stops the bank on exit, including on failure. CI runs it on every push. A
  fresh GitHub runner is the clean container for the fresh-clone check.
- `make demo-handoff`: interactive, with a timeout on every human wait.
- `scripts/evidence/demo_video.sh --record-video`: records only when that flag is passed, and
  only the mock bank. It is built but not run.

**[REVIEW] Held-out discovery check.** One real discovery goal outside the evidence set ("List
member 10482's loans") is run once, and its result, pass or fail, goes into
`evidence/README.md` and REPORT.md.
The alternative was several held-out goals. I rejected it to stay well inside the spend cap; one
honest result shows the pipeline was not tuned to the evidence set.

**The PII scan** reads every text file under `evidence/` (JSON, JSONL, HTML, YAML, Markdown)
for:
- the policy's sensitivity patterns;
- every seeded member's name, number, phone and email, at both tenants.

Docs written by the scripts may name the demo member number from PLAN §0, and only in
`README.md` files.

## 2026-10-16 — P10 + P11 as built

**Every evidence scenario checks its own result.** Each declares what it expects: status, category,
recoveries, share counts, which locator won. The script exits non-zero on a mismatch. 16 scenarios
and the held-out check ran; 15 pass, and 14 is pending approval.

**[REVIEW] Selects keep focus after a choice.** Verifying the discovered post flow failed the first
time. In discovery, Enter on the select submitted the form. The replay's select left focus
elsewhere, so the compiled `press Return` did nothing. A person who picks an option keeps focus
on it, so the surface now focuses the list after selecting. The alternative, giving `press` steps
a target to click first, would have opened the list instead. *(Corrected below: Enter on a list
submits the form only in macOS Chromium.)*

**[REVIEW] A new mock-bank fault: `session_expire_on=<route>`.** Scenario 11 must expire the
session right after Post, whatever the request count of a discovered flow. A one-shot route
trigger does that without counting. The bank is the harness, as with the earlier `maintenance`
fault.
The alternative, expiring after N requests, was rejected because the count depends on the discovered
flow, which made scenario 11 fragile.

**The committed post flow came from discovery.** `capabilities/drafts/member.open_share_account.yaml`
is scenario 03's verified artifact, copied unchanged. Its inputs are named by discovery
(`share_account_type`), so the driver reads replay values from each capability's own input
examples instead of guessing names.

**[REVIEW] The PII scan exempts the PLAN's named synthetic members in two places only.** README
files and copied artifacts may name each tenant's demo and drill member (read from tenant config),
because an artifact's input example is the discovery value by design. Member numbers must stand
alone to count, which stops a sha256 hash from matching. A self-test plants a name, a member
number and an SSN, and checks all three are found.
The alternative was no exemptions. I rejected it because the scan would then fail on correct files.

**Evidence size: 14 MB.** Most of it is discovery screenshots. Scenario 15 keeps only its
aggregate and telemetry, not 20 sets of screenshots.

**Spend so far: about $0.55 over 11 real runs**, against the $25 / 30-run cap. A full
`make evidence` uses 4 more runs (01, 02, 03, 12a) for about $0.25, plus 14 once a capability is
approved.

**Not done, and why.**
- No demo video was recorded: the script exists, as asked.
- `gitleaks` is not installed here, so it was not run.
- No LICENSE was added, and CLAUDE.md's "Voice and framing" was not touched: both are the user's
  call.
- `docs/DESIGN.md` is optional and was skipped: the generated `schemas/*.schema.json` is the
  reference.

## 2026-10-16 — P10 + P11 follow-up: CI on Linux

**CI failed on the P10+P11 commit.** `make demo-offline` stalled in scenario 11 on the Linux
runner, though it passed on macOS. Enter on a focused `<select>` submits its form in macOS
Chromium and opens the list in Linux Chromium. Discovery ran on macOS and recorded "press
Return", so on Linux the post flow never reached the review screen. The scripted operator then
waited out its 120 s for an approval that never came, which hid the failing step.

**[REVIEW] Enter on a list in a form follows the HTML rule, on every host.** For Enter on a
single-choice `<select>` inside a form, the surface applies HTML implicit submission itself and
never sends the key: the form's default button is activated once, by its own `click()`. A
disabled default button means nothing is submitted. With no default button, the form submits
only if at most one field blocks implicit submission. The policy check sees the default button,
so Enter on a list whose form posts is refused at layer 1, not only by the network guard. The
result still names the focused list, so recording and compiling are unchanged. Alternatives:
branching on the platform (a pass on one host would prove nothing about the other), or
recompiling the step into a click on Validate (that edits a verified artifact for a host quirk,
and anything discovered elsewhere would still stall). Extended to text fields later: see "Enter
in a text field: layer 1 sees what it submits" below.

**[REVIEW] `make evidence` replaces a scenario's evidence only when the new run passed.** Each
scenario is built in `runs/evidence_staging/`. On pass or pending it replaces its folder in
`evidence/`; on failure the attempt goes to `runs/evidence_failed/` and the old evidence stays.
Replays run before the scenarios that call the model, a crash fails only its own scenario, and
the exit code is non-zero if any failed. The alternative, deleting `evidence/` first as before,
meant one failed discovery wiped committed evidence that had nothing wrong with it.

**`result.json` is written through the evidence redactor.** It holds statuses, categories, run
ids and paths, and every member number a scenario types is registered for scrubbing, so a
mismatch message can't carry one to disk. Scenario 14 records the question's number, not its
text. A test builds a scenario-14 record and scans it.

**A run that ends before the operator's plan is a mismatch.** In scenarios 03, 10 and 11, once
the run returns, the operator gets 5 s to finish its plan; otherwise the scenario fails with how
the run ended, instead of a timeout.

**The four model scenarios were regenerated too.** 01, 02, 03 and 12a ran again so their records
go through the redactor and name `evidence/`, not the staging folder; 02 and 03 exercised the new
Enter rule in real discovery. Spend is now about $0.91 over 18 real runs.

## 2026-10-16 — Enter in a text field: layer 1 sees what it submits

**[REVIEW] Enter in a text field shows layer 1 what it would submit.** The fields are the input
types the HTML spec lists as "fields that block implicit submission": text, search, tel, url,
email, password, date, month, week, time, datetime-local and number. For Enter in one of these,
inside a form, `PolicyEngine.check()` sees the form's default button. So Enter beside a Post
button needs approval, exactly as clicking Post does. The key itself stays the browser's own,
so the page's keystroke handlers run, and the result still names the focused field. The
alternative, emulating the submission as for lists, was rejected: browsers agree on text
fields, and an emulated key would skip the page's handlers.
- **A textarea** is not on the list: Enter there is a newline and is checked as a key on the
  textarea. So are checkboxes, radios and other controls, where some browsers also submit.
  Layer 2 covers those.
- **A disabled default button** means nothing is submitted, so layer 1 sees the field, not a
  button press.
- **No submit button.** The spec still submits a form with at most one blocking field. I chose
  the conservative reading: layer 1 judges that submission by the form's own method and action
  URL against the route rules, so a form that would `POST /share/post` needs approval with no
  button to name. Alternatives: keep seeing the field, which leaves the case to layer 2 alone;
  or refuse every buttonless submission, which would block ordinary one-field search forms.
- **Refusals name the focused field** in `ActionResult.grounded`, for lists too, and the button
  in the decision's detail. The recorder and compiler read `grounded`. This changes one
  assertion in the list test: it now checks the field and the detail instead of the button.

**Known limit: a page's own Enter handler.** Layer 1 judges what the HTML rule would submit. A
page whose Enter handler cancels the submission can be refused for an action that would not have
posted, which fails safe. A handler that submits somewhere else is caught only by layer 2.

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

**[REVIEW] Waiting for a human does not count against the run deadline.** The deadline bounds what
automation spends, so it is pushed back by however long a human wait took. A human wait has its own
limit (`--human-timeout`, default 15 minutes). If nobody answers in time, the run returns
`needs_human` with the failure attached; nothing is posted. The alternative, one deadline covering
human time too, would make any real takeover fail at 120 s.

**[REVIEW] An operator can take control in two ways.**
- **Pause** is cooperative: automation finishes its current step and yields at the step boundary.
- **Force take** moves the lease at once and bumps the epoch, so automation's next `act()` is
  refused as `not_holder`.

With a desk attached, `not_holder` now means "preempted": the engine waits for the human to hand
back. Without a desk it is still POLICY_BLOCKED.

**[REVIEW] Unattended runs ask a human through the console.** An unattended run never approves its
own irreversible step. With a desk attached, it opens an approval intervention and blocks until a
human approves, denies or times out. `--approve-gates` is still refused with `--unattended`. Deny
gives POLICY_BLOCKED, and a timeout gives `needs_human`; neither posts.

**The commit rule covers operator actions.** Operator input goes through `Surface.act` as
`operator:<name>`, at the controller's current epoch. When an operator's action is irreversible and
done (the policy allows it, audited), the run counts as committed. From then on every non-success
is OUTCOME_UNCERTAIN, and resume never acts on that step again.

**[REVIEW] Resume validation.** PLAN §5 is followed first:
1. If a hard global state shows, the result is HANDOFF_STATE_MISMATCH.
2. Otherwise, take the latest step, from the paused one onwards, whose `expect` holds, and resume
   after it.

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

Replay runs that handler type, and an engine test proves it. The patch is never applied
automatically, and it can be replay-verified only once P8 resolves overlays.

**Not built in P5:** authentication for the console, webhooks, and parked asynchronous runs (all
in REPORT §7). Raw screencast frames go only to the operator's browser and never touch disk.

## 2026-10-16 — P5 as built

**[REVIEW] Intervention notices go to stderr, not stdout.** PLAN §5 says "a stdout line", but
`eagleview replay` reserves stdout for the result JSON a caller parses. The line names the run, the
reason and the console URL. Keeping stdout clean was preferred over following the PLAN wording.

**[REVIEW] The console exposes what the operator sees as text: `GET /api/sessions/{run}/boxes`.** It
returns the visible text with geometry, raw, over loopback, like the screencast. The scripted
operator needs it to find what to click; a person uses the picture. Operators are trusted
(PLAN §6), and nothing from it is written to disk.

**[REVIEW] At most three takeovers per run.** Each needs a human. Without a cap, a screen that keeps
failing would keep asking. After the third, a failure is final.

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

**P4 behaviour holds without a desk.** All P4 replay tests pass unedited: `--escalate` without a
console still parks, and a gated step without `--approve-gates` still fails POLICY_BLOCKED.
`HandoffHolder` is gone; the SessionController's `hand_to` does that job.

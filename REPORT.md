# Eagleview: report

Eagleview turns one LLM-driven discovery of a task in a legacy teller application into a typed,
versioned capability, replayed deterministically with no LLM; a person can take over the same
live session when automation is stuck. The app and its data are fictional.
The core is discover → compile → replay → hand off. Two stretch goals were chosen on purpose:
**cross-tenant reuse** (one capability, two credit unions) and an **agent catalog** (approved
capabilities as tools, also over MCP).

## Architecture

```
            authoring time (LLM allowed)                    run time (no LLM)
  goal --> discovery loop --> trace.jsonl --> compiler --> capability.yaml
           (Claude, pixels;    (grounded       (segment, prune,  (typed, verified,
            recorder grounds    actions,        name, verify,     reviewed)
            every action)       redacted)       probe)                 |
                                                                       v
  caller / agent / MCP --> replay engine --> Surface.act() --> legacy app (browser)
                              |  settle loop, recovery          ^
                              v                                 |
                      SessionController (lease + epoch) <--> operator console
```

- **One process.** The engine, session controller and console share one live browser session;
  production would put a session-host service per browser behind the same lease API.
- **LLM at authoring, never at run time.** `replay`, `surfaces`, `safety` and `control` never
  import `anthropic`, `discovery` or `compiler`; a static plus runtime import-graph test enforces it.
- **What needs a DOM.** The model sees only pixels; the recorder (CDP hit-testing plus the
  accessibility tree) and replay's locators use the DOM. Only css and xpath need one by nature:

  | Locator | Without a DOM (desktop) |
  |---|---|
  | a11y | yes: UI Automation or AX |
  | anchor, text | yes: text geometry from UIA/AX or OCR |
  | css, xpath | no |
  | point | yes, fragile: bound to a screen fingerprint |
- **Read before compiling.** Two real discovery traces were reviewed line by line first: the
  model took a teller's shortest path, and two leaks to disk (a mid-load screenshot, a quoted
  balance) were fixed. Appendix: [docs/DISCOVERY_TRACE_REVIEW.md](docs/DISCOVERY_TRACE_REVIEW.md).

## Artifact schema

- **Three layers, reviewed as YAML.** The **app profile** holds what is true of the product on
  every tenant (fingerprint, sign-on macro, global error states, sensitive screen labels); the
  **capability** is one task; **overlays** are tenant and version patches.
- **Contract first.** Typed inputs with patterns, typed outputs (money is `Decimal`), business
  outcomes, a success predicate. The agent catalog turns this into a tool schema.
- **Steps.** Semantic ids (`enter_member_id`), never indices, so overlays can target them. Each
  step has ranked locators, most semantic first, each verified unique when recorded; a step with
  fewer than two non-point strategies is marked fragile. The ranking follows what survives
  change: labels and their on-screen neighbours outlast restyling and upgrades; legacy pages have
  no test IDs, so css and xpath rank low; raw position comes last, bound to one screen fingerprint.
- **Lifecycle.** `draft` → `verified` (a replay passed) → `approved` (a person signed off; only
  approved capabilities run unattended or reach agents). A content hash excludes provenance, and
  an edited verified or approved file is refused.

## Determinism & error handling

- **Predicate waits only.** The 150 ms poll is the only sleep in replay; a test enforces it.
- **The settle loop.** Every look at the screen classifies it in one order: hard states, declared
  outcomes, interstitials a handler clears, then the step's `expect`. So a modal is reported as
  itself, never as "locator not found".
- **Exactly one element.** Ambiguity is a failure, never "pick the first". Enter on a list follows
  the HTML implicit-submission rule on every host, because Chromium differs between macOS and Linux.
- **Taxonomy.** `rejected` (the caller's fault, before any UI action); `outcome` (MEMBER_NOT_FOUND,
  NOT_PERMITTED, VALIDATION_ERROR: answers, not errors); recovered (success with a recovery
  recorded); `failed` (a closed list of categories, always with the step, what was expected, what
  was observed, a screenshot and a redacted DOM snapshot); `needs_human`.
- **App validation.** The app profile turns an `IBT-2xxx` form code into VALIDATION_ERROR,
  quoting the app ("IBT-2210 MEMBER ALREADY HAS A HOLIDAY CLUB SHARE", `evidence/16`). The
  pattern is limited to IBT-2xxx codes because app-wide states are checked before a capability's
  own outcomes, so a broader pattern would swallow the lookup's IBT-1004 "not found".
- **The commit rule.** Retries only on idempotent steps. Once an irreversible action has been
  sent, every non-success is OUTCOME_UNCERTAIN, with no retry: posting twice is the worst failure
  in banking.
- **Outcome probing.** The compiler tries absent-but-valid inputs, classifies the resulting screen
  with an input-independent regex, and keeps an outcome only once a confirm run proves it.
- **Measured.** "Member not found" returns about 25 ms after the search (`evidence/05`); the
  agent's replay took 1.7 s with no model call.

## Heterogeneity & multi-tenant

- **The Surface protocol** is the seam between the recorded flow and the screen; artifacts never
  mention Playwright.
  A desktop app needs a new surface on UI Automation or AX plus OCR (design-only); the table
  above says which locators carry over.
- **One capability, two tenants.** Copperhill differs in labels, nav order, theme, a Search
  button for the GO image, and a Select Branch screen. Its overlays hold two label aliases, one
  interstitial handler and one step patch, keyed by step id.
- **Drift.** Version patches follow the tenant's declared version; a mismatch with the screen
  fails APP_VERSION_UNSUPPORTED. Each run records which locator won and at what rank;
  `eagleview telemetry` turns a rising rank into a drift signal.
- **Data never crosses tenants:** Lantern Bay's member at Copperhill returns MEMBER_NOT_FOUND.

## Escalation & handoff

- **When a person is called.** Replay: any hard failure, when escalation is on, and every
  irreversible step that needs an approval. Discovery: the model's `request_human`, an
  irreversible action (a person performs it), and a stall: no progress over 4 actions, the action
  or tool-call limit, or 600 s (a person's time not counted). A stall opens a DISCOVERY_STALLED
  takeover and the run ends `needs_human` once the person hands back or the wait runs out; the
  model is not asked to continue, so the limits stay hard caps on cost.
- **The request** holds the capability or goal, the step, the reason, a state summary, a redacted
  screenshot and the operator actions allowed, and is kept, redacted, in the run folder.
- **Lease and fencing epoch.** One holder at a time; every hand-over bumps the epoch, so a
  preempted automation's next action is refused.
- **Operator input** goes through the same `Surface.act`, policy-checked, and into the same run log.
- **Resume validation** looks at the screen and never assumes what the person did: it continues
  after the latest step whose `expect` holds, or fails HANDOFF_STATE_MISMATCH.
- **Human-demonstrated patches.** A person's clicks become a draft overlay, for review.
- **Stubbed for evidence.** In evidence and CI a labelled **scripted operator** answers the real
  console over its HTTP API, so handoffs are reproducible.

## Safety

- **The policy file** holds per-tenant origins, route rules (`/__admin` denied, `POST
  /share/post` irreversible), allowed actions and keys, irreversible label patterns, and
  sensitivity patterns.
- **Two independent layers.** The pre-action check in `Surface.act` sees the grounded label,
  role and URL, including the button that Enter would press. The network guard routes every
  request, follows redirects hop by hop, and spends an approval token once.
- **Irreversible actions, conservatively.** Discovery hands them to a person; replay needs a
  token bound to the run and step; unattended runs never approve themselves. Conservative because
  a missed approval costs a click, while a double post costs a reconciliation.
- **Redaction** by the evidence writer: typed masking of declared fields, value-aware scrubbing
  of inputs, outputs and secrets, patterns, and sensitive screen labels, painted out of
  screenshots including under translucent modals. A snake_case key (a step id, a field name) is
  kept whole unless it contains a registered 4-plus-digit value or a word of a registered name;
  every other key is redacted like a value.
- **Secrets by reference** (`secret://`); the model never sees a password.
- **Prompt injection.** On-screen text is data, never an instruction; in the drill a planted note
  asks for `/__admin/faults`, and obeying it is blocked by both layers.

**Limits, stated honestly:**
- Discovery screenshots go unredacted to the model provider: production needs zero data
  retention, synthetic training tenants or an on-prem model.
- Injected text can still steer the model **within** allowed actions (a wrong member, a wrong
  value marked as output); the verify replay checks the path, not the intent, so a person's review
  of the recipe is the control.
- Label heuristics can miss risky actions (hence the network guard); JavaScript-only side
  effects bypass it.
- Operators are trusted; the console has no login.
- Redaction errs on both sides: personal data shown only under unlisted labels is caught by
  patterns alone, and a button label beside a sensitive label can be over-redacted.

**Cost.** A whole pipeline (discover, compile, verify, probe) costs $0.05–0.07 on
`claude-sonnet-5-5`; the held-out goal ("List member 10482's loans") compiled and verified,
twice. All real spend: about $1.52 over 37 runs.

## Cuts

- **Not built, and why:**
  - a real desktop surface: out of scope for the time, though the seam and locators are
    designed for it;
  - parked asynchronous escalation: a run blocks for its person, keeping sessions simple;
  - a multi-browser session host, webhook routing, console login: production infrastructure, not
    the brief's question.
- **Built beyond the core, as support:** multi-run stability (20 replays, 20 successes, one
  distinct output, 1.29–1.55 s each, `evidence/15`), a spend ledger, the console's screencast, drift telemetry, a
  demo video. The MCP server only wraps the catalog.
- **Next:** `eagleview adapt` (an LLM proposes an overlay patch, verified by replay); read-only
  reconciliation after OUTCOME_UNCERTAIN; session pooling.

# Eagleview: report

Eagleview turns one LLM-driven discovery of a task in a legacy teller application into a typed,
versioned capability. The capability is replayed deterministically with no LLM, and a person can
take over the same live session when automation is stuck. Everything here runs against a
fictional legacy app with synthetic data.

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

- **One process.** The runner is a single Python process. The live browser session is a shared
  object that the engine, the session controller and the operator console all need. In
  production that becomes a session-host service per browser, exposing the same lease API over
  the network.
- **LLM at authoring, never at run time.** `eagleview.replay`, `surfaces`, `safety` and `control`
  never import `anthropic`, `discovery` or `compiler`, and a static plus runtime import-graph test
  enforces it.
- **Grounding.** The model acts on pixels. A recorder grounds every action into multi-strategy
  semantic locators, each verified to resolve uniquely to the element under the pointer.
- **Python and Playwright.** CDP gives reliable hit-testing through framesets and a screencast
  for the console; Pydantic makes the artifacts the source of truth.
- **Trade-offs.** One process means one browser per runner. Escalation blocks the run it belongs
  to.

## Artifact schema

- **Three layers, reviewed as YAML.**
  - The **app profile** holds what is true of the vendor product on every tenant: fingerprint,
    sign-on macro, global error states and sensitive screen labels.
  - The **capability** is one task.
  - **Overlays** are tenant and version patches.
- **Contract first.** A capability declares typed inputs with patterns, typed outputs (money is
  `Decimal`), business outcomes, and a success predicate.
- **Steps.** Steps have semantic ids (`enter_member_id`), never indices, so overlays can target
  them. Each step has ranked locators (a11y, anchor, text, css, xpath, point) that were verified
  unique when recorded. A step with fewer than two non-point strategies is marked fragile.
- **Provenance and lifecycle.** Every capability records who discovered and verified it. It moves
  from `draft` to `verified` (a replay passed) to `approved` (a person signed off). A canonical
  content hash excludes provenance, and an edited verified file is refused. JSON Schema is
  exported for machines.

## Determinism & error handling

- **Predicate waits only.** Replay waits on predicates, never with a fixed sleep. The 150 ms poll
  is the only sleep in the package, and a test enforces it.
- **The settle loop.** Every look at the screen classifies it in one order:
  1. the app profile's hard states;
  2. declared outcomes;
  3. interstitials a handler clears;
  4. the step's `expect`.

  This order also applies while waiting for a target and during sign-on. So a modal is reported
  as itself, never as "locator not found".
- **A locator must resolve to exactly one element.** Ambiguity is a failure, never "pick the
  first".
- **Host-independent input.** Enter on a list in a form follows the HTML implicit-submission
  rule on every host, because Chromium's behaviour differs between macOS and Linux.
- **A true-before `expect`** passes only after a visible change.
- **Taxonomy.**
  - `rejected`: the caller's fault, before any UI action.
  - `outcome`: a legitimate business answer. MEMBER_NOT_FOUND and NOT_PERMITTED are results, not
    errors.
  - recovered: the run succeeds with a recovery recorded.
  - `failed`: a closed list of categories.
  - `needs_human`: parked for a person.
- **The commit rule.** Retries happen only on idempotent steps. Once an irreversible action has
  been sent, every non-success becomes OUTCOME_UNCERTAIN, with no retry and no fresh sign-on.
  Posting twice is the worst failure in banking.
- **Outcome probing.** The compiler proposes absent-but-valid inputs and captures the screen they
  produce. It classifies that screen into an outcome with an input-independent regex, and keeps
  the outcome only if a confirm run proves it.
- **Drift signal.** Locator rank and agreement are recorded per step, and `eagleview telemetry`
  turns them into a drift signal.

## Heterogeneity & multi-tenant

- **The Surface protocol.** Observe, act, ground, resolve, check, extract and text boxes are the
  seam. Artifacts never mention Playwright. Each locator strategy declares the surfaces it
  applies to: css and xpath need a DOM, and anchors work over any text geometry, so a desktop
  surface can use UI Automation or AX plus OCR. The desktop surface is design-only.
- **One capability, two tenants.** The same discovered capability runs on two tenants of the
  product. Copperhill differs in labels, nav order, theme, a Search button instead of a GO image,
  and a Select Branch interstitial.
- **The Copperhill overlay is small.** It holds two label aliases, one interstitial handler and
  one step patch, keyed by step id.
- **Version patches** follow the tenant's declared version. A mismatch with the screen fails
  APP_VERSION_UNSUPPORTED.
- **Data never crosses tenants.** Each tenant has its own members and credentials, so Lantern
  Bay's member replayed at Copperhill returns MEMBER_NOT_FOUND, never a plausible answer.

## Escalation & handoff

- **Stuck detection.** In discovery: no progress over 4 actions, policy blocks, or the model's
  `request_human`. In replay: any hard failure, when escalation is on.
- **Interventions** are kept in SQLite, with a redacted screenshot, a state summary and the
  operator actions allowed.
- **Lease and fencing epoch.** Exactly one holder at a time, and every hand-over bumps the epoch,
  so a preempted automation's next action is refused. Pause is cooperative, at a step boundary;
  force take preempts at the next action.
- **Operator input** relays through the same `Surface.act`, policy-checked and grounded like any
  actor's. Discovery never commits an irreversible action itself: a person performs it in the
  console.
- **Resume validation** looks at the screen and never assumes what the person did. It continues
  after the latest step whose `expect` holds, or re-runs the paused step only if that is safe.
  Otherwise the run fails HANDOFF_STATE_MISMATCH.
- **Human-demonstrated patches.** A person's grounded clicks become a draft
  `human_demonstrated` overlay with a `click_sequence` handler. It is never applied without
  review.

## Safety

- **The policy file** holds per-tenant origins, route rules (`/__admin` denied, `POST
  /share/post` irreversible), allowed actions and keys, irreversible label patterns, and
  sensitivity patterns.
- **Two independent enforcement layers.** The pre-action check in `Surface.act` sees the grounded
  label, role and URL. The network guard routes every request, follows redirects hop by hop, and
  spends an approval token once.
- **Irreversible handling per mode.** Discovery escalates to a person. Replay needs an approval
  token bound to the run and step. Unattended runs never approve themselves.
- **Redaction**, applied by the evidence writer:
  - typed masking of declared fields;
  - value-aware scrubbing of every input, output and secret;
  - patterns;
  - sensitive screen labels from the app profile.

  Screenshots are painted using text boxes read on both sides of the capture, including text
  under translucent modals.
- **Secrets by reference.** Credentials are `secret://` references, and the model never sees a
  password.
- **The injection drill.** A member's notes ask the model to open `/__admin/faults`. The real
  model's run is recorded, and a scripted compliant model is blocked by both layers.

**Limits, stated honestly:**
- Discovery screenshots go unredacted to the model provider. Production needs zero data
  retention, synthetic training tenants or an on-prem model.
- Label heuristics can miss risky actions. That is why the network guard exists.
- JavaScript-only side effects bypass the network guard.
- Operators are trusted, and the console has no login.
- OCR-based redaction on a desktop surface would be weaker.
- Personal data shown only under unlisted labels is caught by patterns alone.

**Measured cost.** A real discovery cost $0.03–0.05. A whole pipeline (discover, compile, verify,
probe) cost $0.04–0.07 on `claude-sonnet-5-5` at medium effort, with caching. The first trace
review is in `docs/DISCOVERY_TRACE_REVIEW.md`.

**Held-out check** ("List member 10482's loans", a goal outside the evidence set): passed. Both
runs gave `member.list_loans@1.0.0`, verified, with MEMBER_NOT_FOUND probed and confirmed, at
$0.045 each. Total real spend across development and evidence was about $0.91 over 18 runs.

## Cuts

- **Not built:**
  - parked asynchronous escalation (persisted `needs_human` runs that resume later);
  - a real desktop surface;
  - a multi-browser session host;
  - webhook routing;
  - real-time co-browsing;
  - console authentication;
  - automated tenant adaptation: an `eagleview adapt` command that would make one bounded LLM
    call per failing step to propose an overlay patch, then verify it by replay.
- **Next:**
  - that adapt command;
  - a per-tenant telemetry dashboard;
  - re-discovery on drift;
  - an approval workflow UI;
  - a run-id breadcrumb written into a memo or nickname field, so a posted transaction can be
    traced to its run;
  - automatic read-only reconciliation after OUTCOME_UNCERTAIN;
  - session pooling for concurrency;
  - a "start here" reading guide.

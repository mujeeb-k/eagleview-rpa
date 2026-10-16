# Morning summary

> **Historical.** A status note written during the build, for the person directing it. It is
> kept for the record and no longer maintained: README.md and REPORT.md describe the project.

Plain-language status, kept current: what is done, every judgment call for you to review, what
is left for you, and the exact commands in order. Last updated after the demo video.

## What is done

Every phase from P4 to P11 is built, tested, committed to `main` and pushed. CI was green on
P4 to P9. On P10+P11 it failed once on Linux, for a real reason I then fixed (see "CI" at the
bottom).

- **Screen-level redaction (before P5).** The app profile lists the labels whose neighbouring
  text is personal (address, notes, phone…). The evidence writer paints that text out of every
  screenshot and scrubs it from every page snapshot. That includes text under a translucent
  pop-up.
- **P5, the person in the loop.** A lease with a "fencing epoch" means exactly one actor
  (automation, discovery or a person) controls the browser, and a stale actor is refused. Requests
  for a person are kept in SQLite, and an operator console shows the live screen with Take
  control, Pause, Resume, Approve, Deny and Abort. Replay can block on a person, check the screen
  after the hand-back, and turn the person's clicks into a draft fix.
- **P6, discovery.** Claude drives the app through the screen, and every click is recorded
  together with the locators that can find that element again. One real run was reviewed before
  building the compiler: `docs/DISCOVERY_TRACE_REVIEW.md`. Spend is capped at $25 and 30 runs.
- **P7, the compiler.** A recording becomes a capability: shortest path, an expected result for
  each step, typed outputs, and names. It is then checked by a real replay, and probed with
  member numbers that don't exist, which teaches it what "member not found" looks like. Real run:
  `member.lookup_share_balances`, verified, for $0.04.
- **P8, the second credit union.** The same discovered capability runs at Copperhill with a
  small reviewed overlay: two label renames, its Select Branch screen, and its Search button.
  Lantern Bay's member at Copperhill returns "not found", never a plausible answer.
- **P9, the agent catalog.** An MCP server and an agent demo expose **approved** capabilities as
  tools. You approved the lookup; the agent now answers through it.
- **P10 and P11, evidence and docs.**
  - 16 evidence scenarios plus a held-out check, each checking its own result: all 16 pass,
    scenario 14 (the agent catalog) included.
  - `make evidence`, `make demo-offline` (no API key, no person; runs in CI on every push) and
    `make demo-handoff`.
  - A PII scan test over `evidence/`, plus README and REPORT drafts.
- **Since then.** Enter in a text field is checked as what it submits (#41); MIT LICENSE;
  CLAUDE.md's voice section rewritten; gitleaks over the full history found nothing.
- **The demo video**, `evidence/demo.mp4`: 3:00, 2.9 MB, linked from the README. Six steps:
  discovery with "AI:" subtitles from the run's own redacted rationale, replay with no model, a
  member who doesn't exist, the Post gate, a person taking over in the console, and the agent.
  The raw clips are kept in `runs/demo_video/`, so it can be re-edited with
  `scripts/evidence/demo_video.sh --build` and no new recording.

**Committed artifacts.** Both came from real discovery and are copied unchanged. I approved
nothing; you approved the lookup.
- `capabilities/member.lookup_share_balances.yaml`: **approved** by you (the draft stays in
  `capabilities/drafts/`).
- `capabilities/drafts/member.open_share_account.yaml`: the post flow, `verified`, where Post needs
  approval.

**Spend.** About $1.26 over 29 real runs. The cap is $25 and 33 runs: you raised the run cap
from 30 for the video, so 4 runs remain. A full `make evidence` uses 6 runs (about $0.30). The
count lives in your local ledger, `runs/discovery_ledger.jsonl`, which has never been edited;
the cap itself is `Limits` in `src/eagleview/discovery/meter.py`.

## Every [REVIEW] item, one line each

These are all 45 judgment calls in `docs/DECISIONS.md` (`grep -n "\[REVIEW\]" docs/DECISIONS.md`).

**Redaction**
1. Sensitive labels are listed per screen, named by its heading, rather than as one global list.
2. "The value beside a label" is found by geometry (row, then column), so a desktop surface can
   reuse it.

**P5, the person in the loop**

3. Without a console attached, P4 behaviour is unchanged (escalate parks; gates are blocked).
4. Time spent waiting for a person doesn't count against the 120 s run deadline; a human wait has
   its own 15-minute limit.
5. Two ways to take control: Pause (at the next step) and Force take (refuses the next action).
6. Unattended runs ask a person to approve through the console; they never approve themselves.
7. Resume validation follows PLAN §5, plus one safe extension: re-run the paused step if its
   target is back and it was never sent.
8. A person's clicks become a draft `human_demonstrated` overlay, never applied automatically.
9. "Someone is needed" notices go to stderr, because stdout carries the result JSON.
10. The console exposes the visible text with positions (`/boxes`), so the scripted operator can
    find things.
11. At most three takeovers per run.
12. The proposed patch's trigger is the first new text on screen, ignoring digit-only changes.

**P6, discovery**

13. Default model `claude-sonnet-5-5` at effort `medium`, both recorded.
14. Server-side refusal fallbacks are on (`fallbacks: "default"`), priced at the dearer model.
15. Toolset members the policy would refuse are not offered (right-click, drag, …).
16. Only screen-changing actions count toward the 40-action cap and the no-progress rule; 80 tool
    calls in total.
17. Discovery waits for the screen to settle before recording an after-state (authoring only).
18. The prompt asks for one sentence of rationale per batch.
19. A marked output is registered for redaction at once, so the model's prose can't leak it.

**P7, the compiler**

20. *(Superseded by 25.)* The plan was to let discovery take an approval.
21. Pruning keeps the shortest path through the screens, and value-setting steps on that path.
22. Outputs fail closed: everything personal by default, money typed as decimal.
23. The naming pass may rename and raise a risk, never lower one or touch locators or predicates.
24. Verifying an irreversible flow needs a person approving in the console; probes never get one.
25. Discovery never posts by itself: a person performs Post in the console, and that click is
    recorded.
26. If Claude declines to suggest probe values, deterministic fallbacks (99999, 00000) are used.
27. A select filled from a declared input binds to that input, because an open list commits late.
28. The final verify runs only if probing added outcomes, so Post isn't repeated for nothing.

**P8 and P9, tenants and the catalog**

29. Version patches follow the version a tenant declares (`app_version`).
30. Copperhill's branch is chosen by name ("Hillcrest"), not by position.
31. The agent catalog lists only approved, non-irreversible capabilities.
32. Copperhill has two overlays: profile-wide (aliases, branch) and one for the lookup (Search).
33. MCP uses the SDK's low-level server, which takes our JSON Schemas as given.

**P10 and P11, evidence**

34. Replay evidence uses the committed artifacts; discovery evidence keeps its own copies.
35. The held-out check: "List member 10482's loans". It passed, verified, both times.
36. After a select, focus stays on the list, as after a person's choice.
37. New mock-bank fault `session_expire_on=<route>`, so scenario 11 doesn't depend on request
    counts.
38. The PII scan allows the PLAN's named synthetic members only in READMEs and artifact copies.

**After CI on Linux**

39. Enter on a list in a form follows the HTML implicit-submission rule on every host, and the
    policy check sees the button it presses. Chromium differs here between macOS and Linux.
40. `make evidence` replaces a scenario's evidence only when its new run passed; a failed attempt
    is kept in `runs/evidence_failed/`.

**Afterwards**

41. Enter in a text field shows the policy check the form's default button, or with no button,
    the form's own method and action against the route rules (the conservative reading).
42. The video's raw clips are kept under `runs/demo_video/` (gitignored), so edits never need a
    new recording.
43. Video zooms target what the run's own final screen shows, read from its text boxes.
44. The video's "In control" label follows the controller's state from the run log: No one
    while waiting, Person while the operator holds it, Automation otherwise.
45. On-screen reading time is per piece of text; pieces shown together share the longer time.

## What is left for you

**Review**
1. **Watch `evidence/demo.mp4`** (3:00). Captions I changed to match the footage are listed in
   docs/DECISIONS.md, "The demo video, re-edited".
2. **The post flow draft**, `capabilities/drafts/member.open_share_account.yaml`, is still
   `verified`. Approving it is optional: the agent catalog leaves irreversible capabilities out
   by design, and replay of it always needs an approval at Post.
3. **The [REVIEW] items above**, especially #41 to #45, which are new since your last review.

**Decide**
4. **The repository is private**, but the video's closing card shows its URL. Make it public,
   or tell me to change that card (a free `--build`, no new recording).
5. **The agent demo's one run in Part 4 failed** with APP_UNAVAILABLE, because the mock bank
   wasn't running. The script now refuses before spending when the bank is down. Rerunning it
   with `make bank` up costs one run; the video and scenario 14 already show it working.

**Check**
6. **README and REPORT are drafts.** The release checklist asks for README commands
   copy-pasted from a real terminal session: please run them once yourself.

## Known issues

- **The console banner trails the controller by up to a second**, because the page refreshes
  once a second. The video's "In control" label is exact; the banner beside it can lag.
- **A page's own Enter handler** can make layer 1 refuse an Enter that wouldn't have posted
  (#41). It fails safe; a handler that submits elsewhere is caught only by layer 2.
- **`verified_by` timestamps:** fixed; each entry now carries its own run's completion time.

## Commands, in order

```bash
uv sync
```

```bash
make demo-offline
```

```bash
make check
```

```bash
make bank
```

```bash
PYTHONPATH=. uv run python scripts/agent_demo.py "What's in member 10482's savings?"
```

```bash
make demo-handoff
```

- `make demo-offline` needs no key and no person.
- `make check` runs lint, types and the tests, including the PII scan over `evidence/`.
- The agent demo needs the mock bank: start `make bank` in another terminal first. It spends
  one run.
- `make demo-handoff` is optional: take over a stuck run yourself at http://127.0.0.1:8700.
- `make evidence` regenerates everything (6 runs, about $0.30); a scenario that fails keeps its
  old evidence and makes the command exit non-zero.
- `scripts/evidence/demo_video.sh --build` re-edits the video from the kept raw clips, free.

## CI

- **P4 to P9:** green on every phase commit.
- **P10+P11 (`3185db1`): failed**, in `make demo-offline`, scenario 11, on the Linux runner
  (run 38860309441). Enter on a focused list submits its form in macOS Chromium but opens the
  list in Linux Chromium. Discovery ran on macOS, so the recorded "press Return" stalled on
  Linux. The driver also hid the failing step behind a 120 s timeout.
- **The fix (`06fbb62`): green** (run 38863367145). The surface now applies the HTML
  implicit-submission rule on every host (#39), and all ten offline scenarios pass on Linux.
- **The evidence regeneration (`b797bf4`): failed** (run 38865333365). A path fix I made in
  that commit crashed on `make demo-offline`'s relative `--out runs/demo`. I had not rerun
  `demo-offline` locally after that change. The next commit fixes it, with a test.
- **The relative-path fix (`b438473`): green** (run 38867482127).
- **Enter in a text field, LICENSE and docs (`506124b`): green** (run 38900825158).
- **Evidence with the approved lookup (`2f03b63`): green** (run 38904314299).
- This file is in the demo-video commit; its CI result is in my closing message, because a file
  can't report the CI run of the commit that contains it.

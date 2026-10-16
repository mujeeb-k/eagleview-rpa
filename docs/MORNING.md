# Morning summary

Plain-language status after the overnight run, as you asked: what is done, every judgment call
for you to review, what is left for you, and the exact commands in order.

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
  tools. They are built and tested offline; none are approved yet.
- **P10 and P11, evidence and docs.**
  - 16 evidence scenarios plus a held-out check, each checking its own result: 15 pass, and 14
    waits for your approval.
  - `make evidence`, `make demo-offline` (no API key, no person; runs in CI on every push) and
    `make demo-handoff`.
  - A PII scan test over `evidence/`, plus README and REPORT drafts.

**Committed artifacts.** Both came from real discovery, are copied unchanged, and stay
`verified`. I approved nothing.
- `capabilities/drafts/member.lookup_share_balances.yaml`
- `capabilities/drafts/member.open_share_account.yaml`, the post flow, where Post needs approval

**Spend.** About $0.91 over 18 real runs, out of your $25 / 30-run budget, so 12 runs remain.
A full `make evidence` uses 4 runs (about $0.25), and 6 once scenario 14 can run (its two
agent questions), about $0.30. The cap lives
in your local ledger, `runs/discovery_ledger.jsonl`. It is unchanged; to reset it, move that file
aside. There was no blocker, so there is no `docs/STATUS.md`.

## Every [REVIEW] item, one line each

These are all 40 judgment calls in `docs/DECISIONS.md` (`grep -n "\[REVIEW\]" docs/DECISIONS.md`).

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

## What is left for you

**Review and approve**
1. **Review the two verified drafts** in `capabilities/drafts/`. Each holds its steps, locators,
   expected results, outputs, and the probed MEMBER_NOT_FOUND outcome. Approving is your call
   alone.
2. **Approve** what you're happy with. Scenario 14 and the agent demo need an approved lookup.

**Decide**
3. **LICENSE:** done, MIT, in your name, 2026.
4. **CLAUDE.md's "Voice and framing":** rewritten in neutral terms, as you asked. The README now
   has a "How this was built" section.
5. **The demo video:** run `scripts/evidence/demo_video.sh --record-video` when you want it. It
   needs `ffmpeg`.

**Check**
6. **`gitleaks`** 8.30.1 scanned the full git history (25 commits at the time): no leaks.
7. **Enter on a text field** is now checked as what it submits: the default button, or with no
   button, the form's own action (decision "Enter in a text field"). A page's own Enter handler
   can still make layer 1 refuse something that wouldn't have posted; that is a known limit.
8. **README and REPORT are drafts.** REPORT is about 1,450 words under PLAN §9's headings, and a
   test checks them. The release checklist asks for README commands copy-pasted from a real
   terminal session: please run them once yourself.

## Known issues

- **`verified_by` timestamps.** In a compiled capability, every `verified_by` entry carries the
  same `at`. The compiler takes one clock reading after all verify replays and stamps it on
  each (`src/eagleview/compiler/pipeline.py`, where `verified_by` is built). Each entry should
  carry its own run's end time. It doesn't affect replay or the content hash (provenance is
  excluded). Not fixed yet.

## Commands, in order

```bash
uv sync
```

```bash
make demo-offline
```

```bash
uv run eagleview approve capabilities/drafts/member.lookup_share_balances.yaml --reviewer <your-name>
```

```bash
make evidence
```

```bash
make check
```

```bash
PYTHONPATH=. uv run python scripts/agent_demo.py "What's in member 10482's savings?"
```

```bash
make demo-handoff
```

- `make demo-offline` needs no key and no person.
- After the approve, `make evidence` regenerates everything and now runs scenario 14 for real.
  It spends about $0.30. A scenario that fails keeps its old evidence and makes the command
  exit non-zero; the failed attempt is in `runs/evidence_failed/`.
- `make check` afterwards runs the PII scan over the new evidence.
- The agent demo answers through the catalog. It spends a few cents.
- `make demo-handoff` is optional: take over a stuck run yourself at http://127.0.0.1:8700.

The post flow can be approved the same way, but the catalog leaves irreversible capabilities
out, by design.

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
- This file is in that next commit; its CI result is in my closing message, not
  here, because a file can't report the CI run of the commit that contains it.

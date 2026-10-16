# Eagleview

**An AI learns a task in a bank's legacy teller software once; after that, a reviewed recipe
runs it the same way every time with no AI, and a person can take over the live screen when it
gets stuck.**

[![The demo, 1:48, narrated: click to play](evidence/demo_poster.png)](evidence/demo.mp4)

Narration is AI-generated.

**The whole thread, in five lines:**
1. **Discover:** Claude drives the teller app through screenshots, once (`evidence/01_discovery_member_lookup/`).
2. **Compile:** the run becomes a typed, versioned recipe, verified by a replay and probed for "member not found" (`capabilities/drafts/`).
3. **Approve:** a person reviews the YAML and approves it (`capabilities/member.lookup_share_balances.yaml`).
4. **Replay:** the recipe runs with inputs, typed outputs and classified errors, no AI on the path (`evidence/04`, `05`, `09`).
5. **Hand off:** when the screen surprises it, a person takes over the same live session and hands it back (`evidence/10_replay_takeover/`).

**Discover, then replay what was discovered** (with `make bank` running, and a key in `.env`):

```bash
uv run eagleview discover --goal "Look up member 10482 and read their share balances."
```

```bash
uv run eagleview replay capabilities/drafts/member.lookup_share_balances.yaml --input member_id=10482
```

`discover` prints `written to <path>`: replay that path (a new draft never overwrites one, so a
second discovery writes `….2.yaml`).

**Measured:**
- 20 of 20 replays succeeded with one distinct output, in 1.29–1.55 s each (`evidence/15`).
- "Member not found" comes back about 25 ms after the search, as an answer, not an error (`evidence/05`).
- Answering through the agent catalog: 0 AI calls during the replay, 1.7 s (shown in the demo).
- An app validation error comes back as an answer quoting the app, `VALIDATION_ERROR: IBT-2210
  MEMBER ALREADY HAS A HOLIDAY CLUB SHARE`, not a timeout (`evidence/16`).
- One discovery plus compile, verify and probe: $0.05–0.07 on `claude-sonnet-5-5`; all real
  spend so far, about $1.52 over 37 runs.
- 690 tests; 17 self-checking evidence scenarios, all passing; CI runs the offline demo on every push.

All institutions, people and data here are fictional and synthetic: "Ironbridge Teller 7" is a
mock legacy teller system in `mock_bank/`, run as two credit unions.

## Hard problems I hit

- **Enter behaved differently on macOS and Linux.** In Chromium, Enter on a focused `<select>`
  submits its form on macOS and opens the list on Linux. Discovery ran on a Mac and recorded
  "press Return", so the replay stalled on CI. The surface now applies the HTML
  implicit-submission rule itself on every host, and the policy check sees the button Enter would
  press, so Enter can't post past it (docs/DECISIONS.md, "Enter on a list in a form").
- **A modal hid personal data from redaction.** A translucent alert covered the member's details;
  text under it wasn't "visible", so it wasn't painted out of screenshots, though it was
  readable. Redaction now reads every text box, covered ones included, just before and again just
  after each screenshot, and paints out everything either reading found.
- **A safety rule the plan didn't anticipate: discovery can never post.** A safety test pins
  that discovery never commits an irreversible action. Rather than weaken it, a person performs
  Post in the console, and their click is recorded into the recipe.
- **A redactor that corrupted field names.** At the second credit union, "Search" sat beside a
  sensitive label and was scrubbed out of the step id `submit_member_search`. A snake_case key is
  now kept whole unless it contains a registered 4-plus-digit value or a word of a registered
  name; values, and keys of any other shape, are scrubbed as before.

## Setup

You need Python 3.12 and [uv](https://docs.astral.sh/uv/). Node 20+ and `ffmpeg` are needed only
to re-render the video (`video/`).

```bash
uv sync
```

```bash
uv run playwright install chromium
```

```bash
make bank
```

`make bank` serves both credit unions, Lantern Bay on http://127.0.0.1:8601 (operator
`teller01`) and Copperhill on http://127.0.0.1:8602 (`tlr2210`), until you stop it; leave it
running in its own terminal for the commands below. Missing passwords are generated into the
gitignored `.env`.

**For discovery and the agent demo only**, put your Anthropic API key in `.env`
(`ANTHROPIC_API_KEY=…`) or the environment. It spends your own credit; every call goes to
`runs/discovery_ledger.jsonl`, and discovery stops at $25 or 45 runs.

## Without any key: the offline demo

```bash
make demo-offline
```

It starts its own bank, replays the committed recipes through ten scenarios (success, not found,
rejected input, a dismissed notice, a session expiry recovered, a hard app error, a takeover, a
post made uncertain by an expiry with no retry, the injection drill, the second credit union) and
stops. A **scripted operator** answers the console, so no person is needed.

## Replay

The replay command above returns JSON on stdout and a summary on stderr; evidence goes to
`runs/<run_id>/`, redacted. Exit codes: 0 success, 3 business outcome, 4 rejected, 5 failed,
6 needs a human. `--tenant copperhill --input member_id=2210236` runs the same recipe at the
second credit union with its overlays. Post needs an approval (`--approve-gates`, or a person in
the console), and after a post anything but success is `OUTCOME_UNCERTAIN`, never retried.

## A person in the loop

```bash
make demo-handoff
```

Open http://127.0.0.1:8700, take control, tick "Supervisor notified", click Continue, and resume.
Automation checks the screen before carrying on; your clicks become a draft fix for review. Every
request for a person is kept, redacted, in its run folder, and a discovery that stalls (no
progress, a limit, or 600 s) asks for a person too, then stops.

## More

- [REPORT.md](REPORT.md): the design in three pages.
- [evidence/README.md](evidence/README.md): every scenario, what it shows, and how to rerun it.
- [docs/DECISIONS.md](docs/DECISIONS.md): every judgment call, with the alternative considered.
- `make check`: lint, types and the tests. `make evidence` regenerates all evidence (6 paid runs).

## How this was built

Built with Claude Code under my direction: [CLAUDE.md](CLAUDE.md) holds my standing
instructions and invariants, [PLAN.md](PLAN.md) the phased plan, and docs/DECISIONS.md each
judgment call, whose [REVIEW] items I reviewed. Approving a capability is always a person's step.

MIT licensed; see [LICENSE](LICENSE).

# Eagleview

[![The demo: an AI learns a bank task once, then a recipe runs it with no AI (1:50, click to play)](evidence/demo_poster.png)](evidence/demo.mp4)

Eagleview automates legacy bank and credit-union back-office applications that have no API. An
LLM discovers how to do a task once, through the screen, and the run is compiled into a typed,
versioned **capability artifact**. Production replays that artifact **deterministically, with no
LLM**, and a person can take over the same live session when automation is stuck, then hand it
back.

**The demo ([evidence/demo.mp4](evidence/demo.mp4), 1:50, narrated, with subtitles)** follows
one member's question from start to finish: the model learning the lookup once, the approved
recipe answering with no model, a member number that doesn't exist, the approval gate on Post, a
person taking over the live session, and the same recipe at a second credit union. It is filmed
from the mock bank only and rendered by `scripts/evidence/demo_video.sh` (Remotion, in
[video/](video/README.md)). Narration is AI-generated.

All institutions, people and data in this repository are fictional and synthetic. The app being
automated is "Ironbridge Teller 7", a mock legacy teller system in `mock_bank/`, run as two
fictional credit unions.

## Quickstart

```bash
uv sync
```

```bash
uv run playwright install chromium
```

```bash
make bank
```

`make bank` (`uv run python -m mock_bank`) starts both tenants: Lantern Bay FCU on
http://127.0.0.1:8601 (operator `teller01`) and Copperhill CCU on http://127.0.0.1:8602
(`tlr2210`). Missing passwords are generated into the gitignored `.env`. Tested on macOS
(development) and Linux (CI, Ubuntu).

## Offline demo first: no API key

```bash
make demo-offline
```

Same as `PYTHONPATH=. uv run python scripts/evidence/driver.py 04 05 06 07 08 09 10 11 12b 13 --out runs/demo`.
It starts the bank in-process, stops it on exit, and replays the committed artifacts through the
main scenarios:
- success with typed outputs;
- member not found;
- a rejected input;
- a dismissed system notice;
- a session expiry recovered by signing on again;
- a hard app error;
- a takeover by the operator;
- a post that becomes `OUTCOME_UNCERTAIN` after an expiry, with no retry;
- the injection drill;
- the second tenant.

The operator console steps are answered by a **scripted operator** that uses the console's HTTP
API, so no person is needed. CI runs this on every push.

## Replay

```bash
uv run eagleview replay capabilities/drafts/member.lookup_share_balances.yaml --input member_id=10482
```

The result JSON goes to stdout and a summary to stderr. Evidence goes to `runs/<run_id>/`,
redacted: event log, screenshots, summary. `uv run eagleview view runs/<run_id>` writes a timeline
page for one run.

| Exit code | Meaning |
|---|---|
| 0 | success |
| 3 | business outcome (MEMBER_NOT_FOUND, NOT_PERMITTED) |
| 4 | rejected before the UI was touched |
| 5 | failed |
| 6 | needs a human |

- **Input normalization.** String inputs are trimmed of surrounding whitespace, then validated
  strictly. Nothing else changes: no case folding, digit extraction or padding. `" 10482 "` runs
  as `10482`; `"10 482"` is rejected as `INPUT_INVALID`.
- **Irreversible steps.** Post needs an approval: `--approve-gates` for this run, or a person in
  the operator console. `--unattended` runs need an `approved` capability and never approve
  their own irreversible steps. After an irreversible step has been sent, anything other than
  success is `OUTCOME_UNCERTAIN`, never retried.
- **Edited artifacts.** A verified or approved file edited after review is refused. Set
  `status: draft`, or pass `--allow-modified` (recorded as `modified: true`, refused unattended).
- **Tenants.** The tenant's overlays in `overlays/<tenant>/` apply by default (`--no-overlays` to
  turn them off), so the same capability runs at Copperhill:

```bash
uv run eagleview replay capabilities/drafts/member.lookup_share_balances.yaml --tenant copperhill --input member_id=2210236
```

- **Other options.** `--deadline` bounds the whole run (default 120 s), and time spent waiting
  for a person doesn't count. `--headed` shows a slowed browser. `--escalate` hands a hard
  failure to a person.

## Operator console

```bash
make demo-handoff
```

Same as `PYTHONPATH=. uv run python scripts/demo_handoff.py`. It arms an unknown "Cash Drawer
Alert" modal and replays the lookup with escalation on. When it stops, do this:
1. Open http://127.0.0.1:8700 and the session page, where the live screen streams.
2. Click **Take control**, tick "Supervisor notified" on the screen, then click Continue.
3. Click **Resume**. Automation checks the screen before carrying on; it never assumes what you
   did.

Your clicks are written to `runs/<run_id>/proposed_overlay.yaml`, a draft handler for review
that is never applied automatically. A run waits 5 minutes for a person, then is parked as
`needs_human`. The console also does Force take, Pause, Approve, Deny and Abort. It binds to
loopback and has no login: operators are trusted.

## Full demo: discover, then replay

```bash
make demo-full
```

Same as `PYTHONPATH=. uv run python scripts/evidence/driver.py 01 04 --out runs/demo-full`, or
directly, with `make bank` running:

```bash
uv run eagleview discover --goal "Look up member 10482 and read their share balances."
```

Claude (`claude-sonnet-5-5` by default; `EAGLEVIEW_MODEL` or `--model` to change) drives the app
once. The trace is then compiled, verified by a replay, and probed for outcomes such as "member
not found". The result is written as a `verified` draft under `capabilities/drafts/`, never
overwriting a file.

**Discovery spends your own Anthropic API credit.** Measured on real runs:
- about $0.03–0.05 for discovery alone;
- about $0.04–0.07 for the whole pipeline (discover, compile, verify, probe).

Every call is recorded in `runs/discovery_ledger.jsonl`, and discovery stops at $25 total or 33
runs.

**Approving is a person's step:**

```bash
uv run eagleview approve capabilities/drafts/member.lookup_share_balances.yaml --reviewer <you>
```

That promotes the draft into `capabilities/` as `approved`. Only approved capabilities run
unattended or reach the agent catalog:
- `uv run eagleview mcp` serves them over MCP;
- `PYTHONPATH=. uv run python scripts/agent_demo.py "What's in member 10482's savings?"` has
  Claude answer through them.

## Evidence

`make evidence` regenerates `evidence/` from scratch, one folder per scenario, indexed in
[evidence/README.md](evidence/README.md). It uses the real model for scenarios 01, 02, 03 and 12a,
and for 14 once a capability is approved. `uv run eagleview telemetry runs/` shows which locator
won per step across runs: a drift signal.

## Repository map

| Path | What |
|---|---|
| `src/eagleview/replay/` | the replay engine: settle loop, recovery, sign-on (no LLM) |
| `src/eagleview/surfaces/` | the Surface seam; `web/`: Playwright, grounding, resolvers, network guard |
| `src/eagleview/safety/` | policy engine, redaction, secrets |
| `src/eagleview/control/` | session lease and fencing epoch; interventions |
| `src/eagleview/operator/` | operator console and scripted operator |
| `src/eagleview/discovery/` | the Claude computer-use loop and recorder (authoring only) |
| `src/eagleview/compiler/` | trace to capability: segment, prune, name, verify, probe |
| `src/eagleview/overlays/`, `catalog/` | tenant overlays; agent catalog and MCP server |
| `src/eagleview/schema/`, `evidence/` | Pydantic models; the redacting evidence writer |
| `capabilities/`, `app_profiles/`, `overlays/`, `tenants/`, `policies/` | reviewable YAML |
| `mock_bank/` | the fictional legacy app under test |
| `evidence/`, `scripts/evidence/` | generated evidence and the scripts that make it |
| `REPORT.md`, `PLAN.md`, `docs/DECISIONS.md` | the design report, plan and decision log |

`make check` (`uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run
pytest`) runs lint, types and the tests.

## How this was built

Eagleview was built with Claude Code under my direction. [CLAUDE.md](CLAUDE.md) holds my
standing instructions and invariants, and [PLAN.md](PLAN.md) is my build plan, phase by phase.
Every judgment call the plan didn't settle is recorded in [docs/DECISIONS.md](docs/DECISIONS.md)
with the alternative considered; the ones tagged [REVIEW] are calls I reviewed myself. Approving a
capability is always a person's step: nothing in `capabilities/` was approved by the tooling.

## License

MIT; see [LICENSE](LICENSE).

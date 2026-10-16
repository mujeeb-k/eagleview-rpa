# Eagleview

Work in progress. See PLAN.md for the build plan.

## Mock bank

`make bank` starts "Ironbridge Teller 7", the fictional legacy teller app Eagleview automates, as two
tenants: Lantern Bay FCU on http://127.0.0.1:8601 (operator `teller01`) and Copperhill CCU on
http://127.0.0.1:8602 (operator `tlr2210`). Each tenant has its own members and its own password.
On first run, `make bank` generates both passwords into `.env` (`LANTERN_BAY_TELLER_PASSWORD`,
`COPPERHILL_TELLER_PASSWORD`), so there is nothing to set up.

## Replay

`eagleview replay` runs a capability deterministically, with no LLM. It opens the app, reads the
running version, signs on with the tenant's credentials, and runs each step. Before a step counts
as done, the screen must show what that step expects. With the mock bank running (`make bank`):

```bash
uv run eagleview replay tests/fixtures/capabilities/member.lookup_share_balances.yaml --input member_id=10482
```

The fixture above is hand-written for testing. Real capabilities come from discovery.

- **Output.** The result JSON goes to stdout and a short summary to stderr. Evidence (event log,
  screenshots, summary) goes to `runs/<run_id>/`, redacted.
- **Exit codes:**

  | Code | Meaning |
  |---|---|
  | 0 | success |
  | 3 | business outcome, such as MEMBER_NOT_FOUND or NOT_PERMITTED |
  | 4 | rejected before the UI was touched |
  | 5 | failed |
  | 6 | needs a human |

- **Input normalization.** String inputs are trimmed of surrounding whitespace, then validated
  strictly against the capability's input schema. There is no other coercion: no case folding, no
  digit extraction, no zero padding. `" 10482 "` runs as `10482`; `"10 482"` is rejected as
  `INPUT_INVALID`.
- **Irreversible steps** such as Post need `--approve-gates`, which approves them for this run only.
  `--unattended` runs need an `approved` capability and never approve their own irreversible steps.
  Once an irreversible step has been sent, replay never retries: anything other than success is
  reported as `OUTCOME_UNCERTAIN`, for a person to check in the app.
- **Edited artifacts.** A verified or approved file that was edited after review is refused. Set
  `status: draft` to experiment, or pass `--allow-modified` to run it as a draft. Such a run records
  `modified: true` and is refused for unattended runs.
- **Other options.** `--deadline` bounds the whole run (default 120 s). `--headed` shows the browser,
  slowed down so a person can follow it. `--escalate` parks a hard failure as `needs_human`.

The passwords come from `.env`, which `make bank` writes.

All institutions, people, and data in this repository are fictional and synthetic.

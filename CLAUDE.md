# CLAUDE.md — Eagleview

## What this is
Eagleview is a computer-use automation system for legacy bank and credit-union back-office applications
that have no API. An LLM discovers how to do a task once. The run compiles into a typed, versioned
**capability artifact**. Production replays the artifact **deterministically with no LLM**. A human can
take over the **same live session** when automation is stuck, then hand it back.
Full build plan: PLAN.md. Decision log: docs/DECISIONS.md.

## Voice and framing
Eagleview is a standalone prototype. Every document in the repo (README, REPORT, evidence index,
DECISIONS, code comments, commit messages) describes what Eagleview is, how it works, and why it was
designed this way, written in the first person by the project's author. Name no real companies, banks,
or vendors; all institutions and data are the fictional ones defined in PLAN.md §1.

## Non-negotiable invariants
1. **No LLM on the replay path.** `eagleview.replay`, `eagleview.surfaces`, and `eagleview.safety`
   must never import `anthropic` or `eagleview.discovery`. Enforced by `tests/test_import_boundaries.py`.
2. **LLMs are allowed only at authoring time**: discovery, compile-time naming, outcome probing, and tenant
   adaptation. Anything an LLM contributes to an artifact is marked `source: llm_proposed` until a
   deterministic replay verifies it.
3. **One choke point for actions.** Every UI action, whether from the LLM, replay, or a human operator,
   goes through `Surface.act()`. That method calls `PolicyEngine.check()` and `SessionController.assert_holder()`
   first. Nothing else touches the browser.
4. **Nothing sensitive reaches disk unredacted.** All log and evidence writes go through
   `eagleview.evidence.writer`, which applies `eagleview.safety.redaction`. Raw screenshots stay in memory.
   Playwright traces, videos, and HAR files are OFF by default, because they contain raw DOM and PII.
   One exception: an opt-in demo video of the synthetic mock bank, recorded only by
   `scripts/evidence/demo_video.sh --record-video` (see docs/DECISIONS.md, "Demo video exception").
5. **Secrets** never appear in the repo, in prompts, or in artifacts. Credentials are written as
   `secret://<name>` and resolved from the environment at runtime. The LLM never sees a password: the
   harness logs in before discovery starts.
6. **Money is `Decimal`**, serialized as a string. Never float.
7. **Evidence is real.** Everything under /evidence is produced by `scripts/evidence/*.sh`. Never hand-write
   or hand-edit it. If a run is wrong, fix the code and re-run the script.
8. **Business outcomes are not failures.** "Member not found" returns `status: outcome`, never an
   exception or a timeout.
9. **Replay is deterministic.** No `sleep` on the replay path; every wait is a predicate with a timeout.
   A locator must resolve to exactly one element. If it resolves to several, that is a failure, never
   "pick the first".
10. **Fixed documentation paths**: /README.md, /REPORT.md (use the seven headings in PLAN.md §9 exactly
    as written), /evidence/.

## Stack
Python 3.12, uv, Playwright (Chromium), Pydantic v2, ruamel.yaml, FastAPI + Jinja2, Typer, anthropic SDK,
mcp SDK (FastMCP), pytest, ruff, pyright (strict on src/eagleview/schema and src/eagleview/replay).

## Claude API facts (verified 2026-10-15)
Re-verify at https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool before building P6.
- Tools entry is `{"type": "computer_toolset_20260801"}`. No beta header is needed. Claude 5.5 models on
  the Claude API accept ONLY this toolset.
- Claude's calls are `tool_use` blocks whose `name` is the member (`left_click`, `type`, `screenshot`, ...)
  and which carry `"toolset_name": "computer"`. Dispatch on (toolset_name, name).
- A single turn can contain a batch of several blocks. Run them in order and run the policy check before
  each block. Stop at the first failure. Every block still gets a tool_result: blocks after the failure get
  `is_error: true` with exactly: `Not executed: an earlier computer action in this turn failed.`
- Every member tool_result echoes `"toolset_name": "computer"`. Only screenshot and zoom return images;
  the other members return the text "OK".
- Coordinates are in screenshot pixel space. Viewport is 1280x800 and screenshots are 1:1, so no scaling.
  Implement `zoom` with a clipped Playwright screenshot.
- If a batch does not end with a screenshot, attach a fresh screenshot to the last result.
- Put instruction text before the image in user turns.
- Thinking: `{"type": "adaptive", "display": "summarized"}`. Log the summaries as the agent's rationale.
- Do not prune screenshots client-side on 5.5 models. If context grows, use server-side context editing
  (`clear_tool_uses_20250919`) and check its current beta header in the docs.
- Models: default `claude-sonnet-5-5`, configurable to `claude-opus-5-5`.
- We write our own loop rather than using the SDK toolset runner, because the recorder must intercept
  and ground every action.

## Conventions
- src layout: `src/eagleview/...`. Every public function is typed. Pydantic models live in `schema/`.
- Logs are JSONL events: `{ts, run_id, seq, actor, kind, ...}`.
- Errors use the closed taxonomy in `schema/result.py`. Never raise bare exceptions across the replay boundary.
- Step IDs are semantic slugs (`enter_member_id`), never indices. Tenant overlays key on them.
- Tests: unit tests next to the domain; integration tests run real Playwright against the mock bank,
  with no LLM. Discovery is tested with a scripted fake model.

## Working style
- Work one phase of PLAN.md at a time. At the end of each phase: run `make check` (ruff, pyright, pytest),
  tick the phase's boxes in PLAN.md, commit with a conventional message, then STOP and summarize what was
  built, what was decided, and what to review.
- If a design question isn't answered by PLAN.md, choose the simplest option consistent with the
  invariants, add a 2–3 line entry to docs/DECISIONS.md, and continue.
- Never put an API key anywhere but `.env`, which is gitignored.
- Every end-of-phase summary must include a section 'Decisions to understand': the 3–5 most important
  design choices made in that phase, each explained in plain language in 2–3 sentences, including the
  alternative and why it was rejected.
- Work directly on main. No feature branches, worktrees, or PRs. At the end of each phase, after
  make check passes: commit to main and git push origin main, then confirm CI passed. Never force-push
  or rewrite history from here on.

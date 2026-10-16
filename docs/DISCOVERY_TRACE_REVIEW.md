# Discovery trace review

Before writing the compiler (P7), I recorded a real discovery trace and read it line by line. This
is that review. The traces live under `runs/`, which is gitignored, so the relevant parts are
quoted here, already redacted as they were written to disk.

- **Goal** (PLAN §0): "Look up member 10482 and read their share balances." On disk the member
  number reads `[member]`.
- **Where:** Lantern Bay, Ironbridge Teller 7.2.3, the local mock bank.
- **Model:** `claude-sonnet-5-5`, effort `medium`, adaptive thinking with summarized display,
  server-side refusal fallbacks on (`fallbacks: "default"`).

| Run | Result | Turns | Screen-changing actions | Tool calls | Cost | Time |
|---|---|---|---|---|---|---|
| 1, raw | success | 3 | 4 | 10 | $0.031 | 18.8 s |
| 2, after the fixes below | success | 5 | 4 | 10 | $0.044 | 40.7 s |

Total discovery spend so far: $0.08 over 2 runs, against the $25 / 30-run limit.

## Run 1: what the model did

The input extraction returned one input: `member_id`, a string, sensitivity `identifier`.

| # | Turn | Tool | What it hit (grounding) | Screen changed | Notes |
|---|---|---|---|---|---|
| 1 | 1 | left_click (55, 83) | link "Member Inquiry", frame `nav` | yes | 6 candidate locators |
| 2 | 1 | wait 1 s | | | looking only |
| 3 | 1 | screenshot | | | |
| 4 | 2 | left_click (295, 135) | textbox, row header "Member #" | no | clicking a field changes nothing visible |
| 5 | 2 | fill_input `member_id` | textbox (focus) | yes | bound to the input, never typed raw |
| 6 | 2 | left_click (418, 135) | img (the GO button) | yes | |
| 7 | 2 | wait 1 s | | | |
| 8 | 2 | screenshot | | | |
| 9 | 3 | mark_output `share_accounts`, table | a cell in the Nickname column | | caption "Share Accounts"; headers Sfx, Description, Nickname, Balance, Available; 3 rows |
| 10 | 3 | declare_success | heading "Member Detail" | | |

Grounding produced candidates that replay can use directly. For the menu link:

- `a11y` link "Member Inquiry";
- `anchor` below "FUNCTIONS";
- `text` "Member Inquiry";
- css `a[href="/inq"]`, plus an xpath and a point bound to `ironbridge-teller@7.2/lantern-bay`.

The member field and the GO button, which have no accessible name, each got two anchors (right of
"Member #", below "Member Search"), a css selector and an xpath.

The policy allowed every action, and nothing was irreversible.

## What was good

- **Direct.** The model took the shortest path a teller would: menu, field, value, GO, read. It
  never wandered, so there were no dead ends for the compiler to prune.
- **Efficient batches.** It batched sensibly: click, fill, click GO, wait, screenshot in one turn,
  checking the screen after each batch.
- **`fill_input` was used** for the goal value, so the recorder bound the typed text to
  `member_id` by provenance; no auto-binding was needed.
- **The table mark is enough to compile.** The caption plus the header labels is exactly what
  an anchored table extract needs. Where the click landed (an empty Nickname cell) doesn't
  matter.
- **Caching works.** Turns 2 and 3 read 5,936 and 7,434 tokens from cache, which keeps a full
  run at a few cents.
- **The API accepted the request shape** on the first real call: toolset with disabled members,
  custom tools with `strict: true`, fallbacks and top-level `cache_control`.

## What was wrong, and what I changed

1. **The "after" screenshot was taken mid-load.** The screenshot after GO showed a blank Member
   Detail frame, while the paint boxes, read a moment later, matched the loaded page. Here that
   painted empty space. The opposite order would leave text unpainted. Replay had the same race
   for screenshots taken while the screen was changing.
   - Fix (`surfaces/capture.py`): text boxes are read before *and* after every screenshot and
     merged, so anything on screen at either moment is painted. Replay and discovery both use it.
   - Discovery also waits for the screen to settle (the same fingerprint twice, at most 3 s)
     before recording an action's after-state, so the compiler sees the result of the action
     rather than a half-loaded page.
2. **No rationale.** At effort `medium` the model returned neither thinking nor text, so every
   recorded action had `rationale: null`, and a reviewer could not tell why it did anything.
   - Fix: the system prompt asks for one short sentence before each batch.
   - Run 2 shows it working, for example "I'll click the Member # field and enter the member ID."
     and "The member number is in the field, so I'll click GO to run the search."
3. **A logging slip.** The input-types event was logged under the key `inputs`, so typed
   redaction masked the word `string` as `****ring`. Renamed to `declared`.
4. **Found in run 2: the model quotes what it read.** The `declare_success` description said
   "… (S00 $3,019.36, S01 $1,661.07, S10 $74.86) marked", and those balances reached
   `trace.jsonl` readable.
   - Cause: discovery never extracts outputs, so nothing had registered them with the redactor.
   - Fix: when the model marks an output, discovery reads it at once and registers it. A marked
     value is registered as personal data; a marked table's amounts go through the amount rule,
     so they follow the policy's `redact_amounts` switch.
   - An offline test now has the fake model quote a balance and checks that it is masked.

## What the compiler (P7) should expect

- **Drop.** `screenshot`, `wait` and `zoom` are looking only, with `changed: false`.
- **Merge.** A click that changed nothing, followed by `fill_input` on the same field, becomes one
  `fill` step targeting the clicked textbox, with the value `{{ inputs.member_id }}`.
- **Expectations.** A step's `expect` comes from text that appeared after the action and was not
  there before. After the menu click that is "Member Search"; after GO it is "Member Detail" or
  "Share Accounts".
- **The table extract** comes from `mark_output.table`: `anchor_text` is the caption, and
  `columns` come from the headers. The compiler must keep only the columns the output needs:
  Nickname is shown but was never part of the goal.
- **Success.** The predicate comes from the declare_success grounding. Run 1 pointed at the
  "Member Detail" heading, run 2 at the "Share Accounts" caption: both are fine anchors.
- **Run-to-run variation.** Run 2 marked only `share_accounts` and not `member_name`. The goal
  asked for balances, so the compiler must not invent outputs the model did not mark.

## Risks I am watching

- **Raw screenshots go to the model provider.** Discovery sends them unredacted, by design and as
  REPORT §6 must state. Only what is written to disk is redacted.
- **Variation between runs.** Two runs, two slightly different paths (an extra turn in run 2).
  The compiler's pruning by state fingerprint, and verification by replay, are what make the
  artifact independent of that variation.

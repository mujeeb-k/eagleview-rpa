"""Records and assembles the demo video, the one exception to invariant 4 (docs/DECISIONS.md,
"Demo video exception"). Only with --record-video to record, and only against the synthetic
mock bank.

Six segments, each recorded into its own raw folder under runs/demo_video/ (gitignored), so the
film can be re-edited without a new recording:
  discovery  the real model learns the lookup (one paid run: discover, compile, verify, probe)
  replay     the approved lookup for 10482, with no model
  missing    the same lookup for a member number that doesn't exist
  gate       the post flow, stopped at Post because nobody approves it
  handoff    an unknown alert, the operator console taking over (the scripted operator), and
             the hand-back
  agent      an agent answering a question through the catalog (one paid run)

    scripts/evidence/demo_video.sh --record-video --offline     # the four free segments
    scripts/evidence/demo_video.sh --record-video --segments discovery,agent
    scripts/evidence/demo_video.sh --build                      # re-edit from the raw clips

With all six raw segments present the film goes to evidence/demo.mp4; otherwise it is a
rehearsal, with placeholder cards, at runs/demo_video/rehearsal.mp4. Either way a contact sheet
is written next to the raw clips. Every word on screen is rendered from HTML by the headless
browser: nothing from a terminal or `.env` is ever filmed. Subtitles quote the discovery run's
own logged rationale, which the evidence writer has already redacted."""

import argparse
import asyncio
import base64
import html
import json
import re
import shutil
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, async_playwright

from eagleview.artifacts.loader import load_for_run, tenant_overlays
from eagleview.artifacts.yaml_io import load_app_profile, load_policy, load_tenant
from eagleview.catalog.agent import ask
from eagleview.catalog.registry import approved
from eagleview.catalog.runner import Catalog
from eagleview.compiler.pipeline import discover_and_compile
from eagleview.control.interventions import InterventionDesk, InterventionStore
from eagleview.discovery.agent_loop import DiscoveryConfig
from eagleview.discovery.client import make_client, model_from
from eagleview.discovery.meter import Ledger, LedgerMeter
from eagleview.evidence.writer import EvidenceWriter
from eagleview.operator.console import create_console
from eagleview.operator.scripted import LABEL, ScriptedOperator
from eagleview.operator.server import console_server
from eagleview.overlays.resolve import resolve
from eagleview.replay.engine import new_run_id, replay
from eagleview.replay.preflight import RunOptions
from eagleview.runner import environment
from eagleview.safety.policy_engine import PolicyContext, PolicyEngine
from eagleview.safety.redaction import Redactor
from eagleview.schema.action import TextBox
from eagleview.schema.policy import Policy
from eagleview.schema.result import RunResult
from eagleview.schema.tenant import TenantProfile
from eagleview.surfaces.base import HolderGuard, Surface
from eagleview.surfaces.web.launch import HEADED_SLOW_MO_MS, surface_opener
from mock_bank.harness import BankClient, running_banks

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "runs" / "demo_video"
FINAL = REPO / "evidence" / "demo.mp4"
APPROVED_LOOKUP = REPO / "capabilities" / "member.lookup_share_balances.yaml"
OPEN_SHARE = REPO / "capabilities" / "drafts" / "member.open_share_account.yaml"
PROFILE = load_app_profile(REPO / "app_profiles" / "ironbridge-teller.yaml")
SEGMENTS = ("discovery", "replay", "missing", "gate", "handoff", "agent")
PAID = ("discovery", "agent")
DISCOVERY_GOAL = "Look up member 10482 and read their share balances."
QUESTION = "What's in member 10482's savings?"
W, H, FPS = 1280, 800, 25
SPEEDUP = 4.0  # discovery plays at up to 4x; slower only where a subtitle needs reading time
HOLD_S = 4.0
X264 = ["-c:v", "libx264", "-preset", "medium", "-crf", "24", "-pix_fmt", "yuv420p"]
X264 += ["-r", str(FPS), "-an"]
FIT = (
    f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
    f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=white"
)


class Mismatch(Exception):
    pass


def expect(condition: bool, what: str) -> None:
    if not condition:
        raise Mismatch(what)


def read_s(text: str) -> float:
    """How long a piece of text stays on screen: one second per 2.5 words, at least 3."""
    return max(3.0, len(text.split()) / 2.5)


# --- the words -----------------------------------------------------------------------------

# Shortened from the brief to bring the film under 3:00, after the clips' dead time was cut.
OPENING = (
    "Banks still rely on old software that other programs can't connect to, so staff click "
    "through it by hand.",
    "Eagleview lets an AI do that work safely. The AI learns a task once; after that, it runs "
    "the same way every time, without the AI.",
)
DISCLAIMER = (
    "Everything here is a pretend credit union built for testing, with made-up names and numbers."
)
STEPS = {
    "discovery": (1, "Learning"),
    "replay": (2, "Repeating, without the AI"),
    "missing": (3, "A customer who doesn't exist"),
    "gate": (4, "An action that can't be undone"),
    "handoff": (5, "A person takes over"),
    "agent": (6, "An AI assistant uses it"),
}
BEFORE = {
    "discovery": "The AI gets a goal: find the account balances for customer 10482. "
    "Watch it read the screen and click through, like a person would.",
    "replay": "Now the recipe runs with no AI involved. Eagleview follows each step "
    "and checks every screen before moving on.",
    "missing": "Real systems hit problems. Here we ask for a customer number that doesn't exist.",
    "gate": "This recipe opens a new account. Its last step is a Post button, "
    "which can't be undone.",
    "handoff": "Something unexpected: a warning the recipe has never seen pops up, "
    "saying the cash drawer is over its limit.",
    "agent": "An AI assistant is asked: what's in customer 10482's savings?",
}
AFTER = {
    # Adjusted from the brief: an unapproved capability can still be replayed by a person at the
    # CLI, so "before anyone can use it" would overclaim. Approval gates unattended use.
    "discovery": "Done. Eagleview recorded every step as a reusable recipe, called a "
    "capability. A person must approve it before it runs unattended.",
    "replay": "Same balances, returned as exact numbers another program can use.",
    "missing": "Eagleview recognises the bank's 'no member found' message and gives a clear "
    "answer, not a crash.",
    "gate": "Blocked, as intended. Eagleview won't press Post without a person's approval, so "
    "nothing was posted. With the operator console open, the run would wait there for a "
    "person to approve.",
    "handoff": "Eagleview checks the screen, then finishes the task where it left off. What the "
    "person did is saved as a suggested fix for review.",
    "agent": "It answers by running the approved recipe, not by clicking around itself. It can "
    "only use recipes a person approved, and never ones that can't be undone.",
}
# Adjusted from the brief to what the screenshot shows: balances stay readable on this training
# tenant (its policy has redact_amounts: false), so the claim names what is painted out.
REDACTION = "Saved screenshots paint out names, contact details and account numbers automatically."
HANDOFF_MIDDLE = (
    "Eagleview pauses and asks for help. A person takes control of the same live screen, deals "
    "with the warning, and hands control back. Here, a script plays the person."
)
CLOSING = (
    "The AI explores once. A person reviews. After that, the task runs reliably, with a safety "
    "check before every action.",
    "Code, design notes and test evidence: github.com/mujeeb-k/eagleview-rpa",
)


def plain(markdown: str) -> str:
    """The agent's answer without markdown: no emphasis marks, headings or code ticks; line
    breaks kept, list markers kept as plain dashes."""
    text = re.sub(r"(\*\*|__|\*|`)", "", markdown)
    text = re.sub(r"^\s*#+\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-•]\s+", "- ", text, flags=re.MULTILINE)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# --- recording -----------------------------------------------------------------------------

Region = tuple[int, int, int, int]  # x0, y0, x1, y1 in screen pixels


def union(boxes: Sequence[TextBox], pad: int = 12) -> Region:
    return (
        max(0, min(b.x for b in boxes) - pad),
        max(0, min(b.y for b in boxes) - pad),
        min(W, max(b.right for b in boxes) + pad),
        min(H, max(b.bottom for b in boxes) + pad),
    )


def find(boxes: Sequence[TextBox], test: Any, what: str) -> TextBox:
    found = [b for b in boxes if not b.covered and test(b.text)]
    expect(bool(found), f"no {what} on the final screen")
    return found[0]


def span_region(boxes: Sequence[TextBox], top: TextBox, end: TextBox) -> Region:
    rows = [b for b in boxes if b.frame == top.frame and top.y <= b.y < end.y and not b.covered]
    x0, y0, x1, _ = union(rows, pad=10)
    return (x0, y0, x1, end.y - 2)


def table_region(boxes: Sequence[TextBox]) -> Region:
    """Member Detail's Share Accounts table: from its bar to the Loan Accounts bar."""
    top = find(boxes, lambda t: t.startswith("Share Accounts"), "Share Accounts bar")
    end = find(boxes, lambda t: t.startswith("Loan Accounts"), "Loan Accounts bar")
    return span_region(boxes, top, end)


def detail_region(boxes: Sequence[TextBox]) -> Region:
    """The member record, from its heading to the end of the share table."""
    top = find(boxes, lambda t: t == "Member Detail", "Member Detail heading")
    end = find(boxes, lambda t: t.startswith("Loan Accounts"), "Loan Accounts bar")
    return span_region(boxes, top, end)


def banner_region(boxes: Sequence[TextBox]) -> Region:
    return union([find(boxes, lambda t: "IBT-1004" in t, "IBT-1004 banner")], pad=18)


def post_region(boxes: Sequence[TextBox]) -> Region:
    post = find(boxes, lambda t: t.strip() == "Post", "Post button")
    warning = find(boxes, lambda t: "cannot be reversed" in t, "Post warning")
    return union([post, warning], pad=16)


@dataclass
class Recorder:
    browser: Browser
    banks: dict[str, BankClient]
    env: dict[str, str]
    meter: LedgerMeter

    @property
    def bank(self) -> BankClient:
        return self.banks["lantern-bay"]

    def tenant(self) -> TenantProfile:
        return load_tenant(REPO / "tenants" / "lantern-bay.yaml").model_copy(
            update={"base_url": self.bank.url}
        )

    def policy(self) -> Policy:
        return load_policy(REPO / "policies" / "training-teller.yaml").model_copy(
            update={"allowed_origins": {s: [b.url] for s, b in self.banks.items()}}
        )

    async def replay(
        self,
        folder: Path,
        cap_path: Path,
        inputs: dict[str, object],
        *,
        options: RunOptions | None = None,
        desk: InterventionDesk | None = None,
        faults: dict[str, str] | None = None,
    ) -> tuple[RunResult, list[TextBox], Path]:
        """A replay, filmed, with the text on its final screen read just before the page
        closes: the run's own geometry for the zoom. Same engine and opener as run_replay."""
        self.bank.reset()
        if faults:
            self.bank.set_faults(**faults)
        policy, tenant = self.policy(), self.tenant()
        writer = EvidenceWriter(folder / "runs", new_run_id(), Redactor.from_policy(policy))
        effective = resolve(
            load_for_run(cap_path).capability, PROFILE, tenant, tenant_overlays(tenant.slug)
        )
        inner = surface_opener(
            self.browser,
            PolicyEngine(policy),
            sink=writer,
            label_aliases=effective.label_aliases,
            record_video_dir=folder / "video",
        )
        final: list[TextBox] = []

        def opener(holder: HolderGuard, ctx: PolicyContext) -> AbstractAsyncContextManager[Surface]:
            @asynccontextmanager
            async def opened() -> AsyncIterator[Surface]:
                async with inner(holder, ctx) as surface:
                    try:
                        yield surface
                    finally:
                        final[:] = await surface.text_boxes(every=True)

            return opened()

        result = await replay(
            effective.capability,
            inputs,
            profile=effective.profile,
            tenant=tenant,
            open_surface=opener,
            writer=writer,
            environ=self.env,
            options=options,
            desk=desk,
            label_aliases=effective.label_aliases,
            effective_hash=effective.effective_hash,
        )
        return result, final, folder / "runs" / writer.run_id


def fresh(segment: str) -> Path:
    folder = RAW / segment
    shutil.rmtree(folder, ignore_errors=True)
    (folder / "video").mkdir(parents=True)
    return folder


def save(folder: Path, data: dict[str, Any]) -> None:
    (folder / "meta.json").write_text(json.dumps(data, indent=2, default=str) + "\n")


async def rec_discovery(r: Recorder) -> None:
    folder = fresh("discovery")
    r.bank.reset()
    result = await discover_and_compile(
        DiscoveryConfig(goal=DISCOVERY_GOAL, model=model_from(r.env)),
        profile=PROFILE,
        tenant=r.tenant(),
        policy=r.policy(),
        browser=r.browser,
        meter=r.meter,
        environ=r.env,
        runs_root=folder / "runs",
        drafts_root=folder / "artifact",
        record_video_dir=folder / "video",  # discovery's own page only; verify is not filmed
    )
    expect(result.status == "verified", f"pipeline ended {result.status}: {result.notes}")
    cap = result.capability
    assert cap is not None
    save(
        folder,
        {
            "run_dir": str(folder / "runs" / result.discovery.run_id),
            "detail": f"{cap.id}@{cap.version}: {cap.status}",
        },
    )


async def rec_replay(r: Recorder) -> None:
    folder = fresh("replay")
    result, boxes, run_dir = await r.replay(folder, APPROVED_LOOKUP, {"member_id": "10482"})
    expect(result.status == "success", f"replay ended {result.status}")
    rows = (result.outputs or {}).get("share_balances") or []
    assert isinstance(rows, list)
    lines = ["share_balances:"]
    for row in rows:
        assert isinstance(row, dict)
        balance = Decimal(str(row["balance"]))  # type: ignore[index]
        lines.append(f"  {row['share_suffix']}  {row['share_description']:<15}{balance:>10}")  # type: ignore[index]
    shots = sorted((run_dir / "steps").glob("*_after.png"))
    expect(bool(shots), "no saved screenshot of Member Detail")
    save(
        folder,
        {
            "zoom": table_region(boxes),
            "detail": "\n".join(lines),
            "evidence_shot": str(shots[-1]),
            "run_dir": str(run_dir),
        },
    )


async def rec_missing(r: Recorder) -> None:
    folder = fresh("missing")
    result, boxes, run_dir = await r.replay(folder, APPROVED_LOOKUP, {"member_id": "99999"})
    expect(result.outcome is not None, f"replay ended {result.status}, not an outcome")
    assert result.outcome is not None
    expect(result.outcome.code == "MEMBER_NOT_FOUND", result.outcome.code)
    detail = f"status: {result.status} · {result.outcome.code}"
    save(folder, {"zoom": banner_region(boxes), "detail": detail, "run_dir": str(run_dir)})


async def rec_gate(r: Recorder) -> None:
    folder = fresh("gate")
    cap = load_for_run(OPEN_SHARE).capability
    inputs: dict[str, object] = {n: s.example for n, s in cap.inputs.items()}
    r.bank.reset()
    before = r.bank.share_rows("10482")
    result, boxes, run_dir = await r.replay(folder, OPEN_SHARE, inputs)
    expect(result.status == "failed" and result.failure is not None, f"{result.status}")
    assert result.failure is not None
    gate = next(s.id for s in cap.steps if s.requires_approval)
    expect(result.failure.step_id == gate, f"stopped at {result.failure.step_id}, not {gate}")
    expect(r.bank.share_rows("10482") == before, "a share was posted")
    detail = f"status: {result.status} · {result.failure.category} at {gate}"
    save(folder, {"zoom": post_region(boxes), "detail": detail, "run_dir": str(run_dir)})


async def rec_handoff(r: Recorder) -> None:
    """The lookup with an unknown modal armed and escalation on, while a second page films the
    operator console. The holder is polled from the console's own API, as its banner shows it."""
    folder = fresh("handoff")
    (folder / "console").mkdir()
    store = InterventionStore(folder / "interventions.sqlite")
    desk = InterventionDesk(store, human_timeout_s=120, notify=lambda _line: None)
    marks: dict[str, float] = {}
    async with console_server(create_console(desk, folder / "runs"), port=0) as url:
        desk.console_url = url
        op = ScriptedOperator(url)
        context = await r.browser.new_context(
            viewport={"width": W, "height": 900},
            record_video_dir=str(folder / "console"),
            record_video_size={"width": W, "height": 900},
        )
        page = await context.new_page()
        marks["page"] = time.time()
        try:
            await page.goto(url)

            async def operate() -> None:
                run_id = await op.wait_for_session()
                await page.goto(f"{url}/sessions/{run_id}")
                await op.wait_for_intervention("takeover", timeout_s=60)
                marks["stuck"] = time.time()
                await asyncio.sleep(2.5)
                await op.take(run_id)
                await asyncio.sleep(2)
                await op.click_text(run_id, "Supervisor notified", dx=-9)
                await asyncio.sleep(1.5)
                await op.click_text(run_id, "Continue")
                await asyncio.sleep(2)
                await op.resume(run_id, note=f"{LABEL}: cleared the drawer alert.")

            task = asyncio.create_task(operate())
            result, boxes, run_dir = await r.replay(
                folder,
                APPROVED_LOOKUP,
                {"member_id": "10482"},
                options=RunOptions(escalate=True),
                desk=desk,
                faults={"unknown_modal_on": "/member"},
            )
            await task
            await asyncio.sleep(1.5)
        finally:
            await context.close()  # finishes the console's video file
            await op.close()
            store.close()
    (folder / "interventions.sqlite").unlink(missing_ok=True)
    expect(result.status == "success", f"handoff replay ended {result.status}")
    expect(bool(result.interventions), "no intervention was recorded")
    expect((run_dir / "proposed_overlay.yaml").is_file(), "no proposed fix was written")
    resolution = result.interventions[0].resolution
    save(
        folder,
        {
            "zoom": detail_region(boxes),
            "marks": marks,
            "run_dir": str(run_dir),
            "detail": f"status: {result.status} · takeover: {resolution}\n"
            "proposed fix written: proposed_overlay.yaml",
        },
    )


async def rec_agent(r: Recorder) -> None:
    folder = fresh("agent")
    r.bank.reset()
    entries = approved(REPO / "capabilities")
    catalog = Catalog(
        entries,
        profile=PROFILE,
        tenant=r.tenant(),
        policy=r.policy(),
        overlays=tenant_overlays("lantern-bay"),
        runs_root=folder / "runs",
        environ=r.env,
        browser=r.browser,
        record_video_dir=folder / "video",
    )
    answer = await ask(
        QUESTION,
        client=make_client(r.env, Redactor()),
        model=model_from(r.env),
        meter=r.meter,
        catalog=catalog,
    )
    statuses = [c["status"] for c in answer.tool_calls]
    expect("success" in statuses, f"tool calls: {statuses}")
    tools = ", ".join(e.tool_name for e in entries)
    first = folder / "runs" / str(answer.tool_calls[0]["run_id"])  # its clip is the first one
    save(
        folder,
        {"detail": f"tools offered: {tools}\n\n{plain(answer.answer)}", "run_dir": str(first)},
    )


RECORDERS = {
    "discovery": rec_discovery,
    "replay": rec_replay,
    "missing": rec_missing,
    "gate": rec_gate,
    "handoff": rec_handoff,
    "agent": rec_agent,
}


async def record(segments: Sequence[str]) -> None:
    env = environment(REPO / ".env")
    meter = LedgerMeter(Ledger(REPO / "runs" / "discovery_ledger.jsonl"))
    async with async_playwright() as pw:
        # Headless, slowed like --headed so a viewer can follow each action.
        browser = await pw.chromium.launch(headless=True, slow_mo=HEADED_SLOW_MO_MS)
        try:
            with running_banks(REPO / ".env") as banks:
                env |= {b.tenant.password_env: b.password for b in banks.values()}
                rec = Recorder(browser, banks, env, meter)
                for segment in segments:
                    print(f"recording {segment}", flush=True)
                    await RECORDERS[segment](rec)
        finally:
            await browser.close()


# --- the look ------------------------------------------------------------------------------

# Bank paperwork, not a SaaS kit: cheque ink, carbon-copy paper, a rubber-stamp red for a stop
# or a person in control, a "processed" green for automation and success.
INK, PAPER, STAMP, CLEARED, AMBER, MIST = (
    "#0F3D47",
    "#E8EEEC",
    "#B42318",
    "#1F6F4A",
    "#B7791F",
    "#CFE3DE",
)
BASE_CSS = f"""
* {{ box-sizing: border-box; margin: 0; }}
html, body {{ width: {W}px; height: {H}px; -webkit-font-smoothing: antialiased; }}
body {{ font-family: 'Avenir Next', Avenir, system-ui, sans-serif; color: {INK}; }}
.mono {{ font-family: Menlo, monospace; font-variant-numeric: tabular-nums; }}
"""


def esc(text: str) -> str:
    return html.escape(text).replace("\n", "<br>")


def card_html(text: str, step: str | None = None, note: str = "") -> str:
    """A full-screen card. Step cards carry the step's number large: the one bold element,
    because the six steps really are a sequence."""
    aside, width = "", "40ch"
    if step is not None:
        n, name = STEPS[step]
        aside = (
            f"<div class='num'><div class='big'>{n}</div><div class='of'>of 6</div>"
            f"<div class='name'>{esc(name)}</div></div>"
        )
        width = "30ch"
    return f"""<html><head><style>{BASE_CSS}
body {{ background: {PAPER}; display: flex; align-items: center; padding: 0 104px; gap: 72px; }}
.num {{ width: 270px; flex: none; }}
.big {{ font-size: 240px; line-height: .8; font-weight: 700; letter-spacing: -.04em; }}
.of {{ font-size: 26px; font-weight: 500; margin-top: 22px; opacity: .7; }}
.name {{ font-size: 30px; font-weight: 600; line-height: 1.2; margin-top: 10px;
  text-wrap: balance; }}
.text {{ font-size: 46px; font-weight: 500; line-height: 1.3; max-width: {width};
  text-wrap: pretty; }}
.mark {{ position: absolute; left: 104px; bottom: 44px; font-size: 20px; font-weight: 600;
  opacity: .5; }}
.note {{ position: absolute; left: 104px; right: 104px; bottom: 96px; font-size: 28px;
  font-weight: 500; line-height: 1.35; max-width: 42ch; text-wrap: pretty; opacity: .8; }}
</style></head><body>{aside}<p class='text'>{esc(text)}</p>
{f"<p class='note'>{esc(note)}</p>" if note else ""}
<div class='mark'>Eagleview</div></body></html>"""


CORNER_CSS = f"""
.who {{ position: absolute; top: 22px; right: 24px; padding: 10px 18px 10px 14px;
  border-radius: 999px; background: #fff; font-size: 22px; font-weight: 600; color: {INK};
  display: flex; align-items: center; gap: 10px;
  box-shadow: 0 0 0 1px rgba(0,0,0,.08), 0 6px 18px rgba(0,0,0,.18); }}
.dot {{ width: 12px; height: 12px; border-radius: 999px; }}
"""


def corner_html(who: str | None) -> str:
    if who is None:
        return ""
    tone = {"Person": STAMP, "No one": "#8A9A9E"}.get(who, CLEARED)
    return (
        f"<div class='who'><span class='dot' style='background:{tone}'></span>"
        f"In control: {who}</div>"
    )


def bar_html(headline: str, detail: str = "", tone: str = INK, corner: str | None = None) -> str:
    """A caption bar over the footage: the plain headline, and the system's own output
    underneath in small monospace. A stripe on the left carries the outcome's tone."""
    detail_html = f"<div class='detail mono'>{esc(detail)}</div>" if detail else ""
    return f"""<html><head><style>{BASE_CSS}
html, body {{ background: transparent; }}
.bar {{ position: absolute; left: 0; right: 0; bottom: 0; padding: 24px 64px 28px 72px;
  background: rgba(15, 61, 71, .96); color: #fff;
  box-shadow: 0 -1px 0 rgba(255,255,255,.06), 0 -10px 28px rgba(0,0,0,.22); }}
.bar::before {{ content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 8px;
  background: {tone}; }}
.head {{ font-size: 34px; font-weight: 600; line-height: 1.28; max-width: 40ch;
  text-wrap: balance; }}
.detail {{ font-size: 17px; line-height: 1.5; color: {MIST}; margin-top: 12px;
  white-space: pre-wrap; }}
{CORNER_CSS}
</style></head><body>{corner_html(corner)}<div class='bar' id='bar'>
<div class='head'>{esc(headline)}</div>{detail_html}</div></body></html>"""


def subtitle_html(text: str) -> str:
    return f"""<html><head><style>{BASE_CSS}
html, body {{ background: transparent; }}
.sub {{ position: absolute; left: 50%; bottom: 40px; transform: translateX(-50%);
  width: max-content; max-width: 40ch; padding: 14px 24px; border-radius: 14px;
  font-size: 30px; line-height: 1.3; color: #fff; background: rgba(8, 24, 28, .86);
  text-wrap: balance; text-align: center; box-shadow: 0 8px 24px rgba(0,0,0,.25); }}
.ai {{ color: #9FE0C9; font-weight: 700; }}
</style></head><body><div class='sub'><span class='ai'>AI:</span> {esc(text)}</div>
</body></html>"""


def corner_only_html(who: str) -> str:
    return f"""<html><head><style>{BASE_CSS} html, body {{ background: transparent; }}
{CORNER_CSS}</style></head><body>{corner_html(who)}</body></html>"""


def side_by_side_html(live: Path, saved: Path, text: str) -> str:
    def data(p: Path) -> str:
        return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()

    return f"""<html><head><style>{BASE_CSS}
body {{ background: {PAPER}; padding: 64px 64px; }}
.row {{ display: flex; gap: 32px; }}
figure {{ flex: 1; }}
img {{ width: 100%; display: block; border-radius: 6px; outline: 1px solid rgba(0,0,0,.1);
  box-shadow: 0 1px 2px rgba(15,61,71,.12), 0 12px 32px rgba(15,61,71,.16); }}
figcaption {{ font-size: 24px; font-weight: 600; margin-bottom: 14px; }}
.text {{ font-size: 38px; font-weight: 500; line-height: 1.3; max-width: 40ch;
  margin-top: 48px; text-wrap: balance; }}
</style></head><body><div class='row'>
<figure><figcaption>The live screen</figcaption><img src='{data(live)}'></figure>
<figure><figcaption>The saved evidence screenshot</figcaption><img src='{data(saved)}'></figure>
</div><p class='text'>{esc(text)}</p></body></html>"""


# --- assembling ----------------------------------------------------------------------------


@dataclass
class Studio:
    """Renders HTML to PNG and encodes every part of the film to one format, for concat."""

    browser: Browser
    work: Path
    ffmpeg: str
    parts: list[Path] = field(default_factory=list[Path])
    n: int = 0

    def path(self, suffix: str) -> Path:
        self.n += 1
        return self.work / f"{self.n:03d}{suffix}"

    async def png(self, markup: str, *, transparent: bool = False) -> tuple[Path, int]:
        """The rendered PNG, and the caption bar's height if the markup has one."""
        out = self.path(".png")
        page = await self.browser.new_page(viewport={"width": W, "height": H})
        await page.set_content(markup)
        await page.evaluate("document.fonts.ready")
        bar = await page.evaluate("(document.getElementById('bar')||{offsetHeight:0}).offsetHeight")
        await page.screenshot(path=str(out), omit_background=transparent)
        await page.close()
        return out, int(bar)

    def encode(self, args: list[str]) -> None:
        out = self.path(".mp4")
        subprocess.run(
            [self.ffmpeg, "-y", "-loglevel", "error", *args, *X264, str(out)], check=True
        )
        self.parts.append(out)

    def still(self, png: Path, seconds: float) -> None:
        self.encode(["-loop", "1", "-t", f"{seconds:.2f}", "-i", str(png), "-vf", f"fps={FPS}"])

    async def card(
        self, text: str, step: str | None = None, seconds: float | None = None, note: str = ""
    ) -> None:
        png, _ = await self.png(card_html(text, step, note))
        # The step's name and its sentence are two pieces shown together: each gets its time.
        need = read_s(text) if step is None else max(read_s(text), read_s(STEPS[step][1]))
        need = max(need, read_s(note)) if note else need
        self.still(png, seconds or need)

    def clip(
        self,
        src: Path,
        start: float,
        end: float,
        *,
        speed: float = 1.0,
        overlays: Sequence[tuple[Path, float, float]] = (),
        hold: float = 0.0,
    ) -> None:
        """Plays src from start to end (seconds) at speed, with overlays as (png, from, to) in
        the part's own time, then holds the last frame for `hold` seconds."""
        dur = (end - start) / speed + hold
        inputs = ["-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src)]
        chain = (
            f"[0:v]setpts=(PTS-STARTPTS)/{speed},fps={FPS},scale={W}:{H}:"
            f"force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=white"
        )
        chain += f",tpad=stop_mode=clone:stop_duration={hold:.3f}" if hold > 0 else ""
        graph = [chain + "[v0]"]
        for i, (png, t0, t1) in enumerate(overlays, 1):
            inputs += ["-loop", "1", "-i", str(png)]
            graph.append(
                f"[v{i - 1}][{i}:v]overlay=0:0:enable='between(t,{t0:.3f},{t1:.3f})'[v{i}]"
            )
        self.encode(
            [*inputs, "-filter_complex", ";".join(graph), "-map", f"[v{len(overlays)}]"]
            + ["-t", f"{dur:.3f}"]
        )

    def hold(
        self,
        still: Path,
        seconds: float,
        overlays: Sequence[Path],
        zoom: Region | None = None,
        bar_h: int = 0,
    ) -> None:
        """The final frame, held, zooming smoothly onto `zoom` so it sits in the space above the
        caption bar. The still is upscaled 4x first, so the zoom moves without jitter."""
        frames = round(seconds * FPS)
        if zoom is None:
            zp = f"zoompan=z=1:d={frames}:s={W}x{H}:fps={FPS}"
        else:
            k = 4
            x0, y0, x1, y1 = zoom
            room = H - bar_h
            s = min(W / max(1, x1 - x0), room / max(1, y1 - y0), 2.6)
            ww, wh = W / s, H / s
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            left = min(max(0.0, cx - ww / 2), W - ww)
            top = min(max(0.0, cy - (room / 2) / s), H - wh)
            p = f"min(on/{1.6 * FPS},1)"
            ease = f"({p}*{p}*(3-2*{p}))"
            zp = (
                f"scale={W * k}:{H * k},zoompan="
                f"z='{W}/({W}+({ww:.2f}-{W})*{ease})':"
                f"x='{left * k:.1f}*{ease}':y='{top * k:.1f}*{ease}':"
                f"d={frames}:s={W}x{H}:fps={FPS}"
            )
        inputs = ["-i", str(still)]
        graph = [f"[0:v]{FIT},{zp}[v0]"]
        for i, png in enumerate(overlays, 1):
            inputs += ["-loop", "1", "-i", str(png)]
            graph.append(f"[v{i - 1}][{i}:v]overlay=0:0[v{i}]")
        self.encode(
            [*inputs, "-filter_complex", ";".join(graph), "-map", f"[v{len(overlays)}]"]
            + ["-frames:v", str(frames)]
        )

    def last_frame(self, src: Path) -> Path:
        out = self.path(".png")
        subprocess.run(
            [self.ffmpeg, "-y", "-loglevel", "error", "-sseof", "-0.6", "-i", str(src)]
            + ["-update", "1", "-frames:v", "999", str(out)],
            check=True,
        )
        return out


def blank_until(ffmpeg: str, src: Path) -> float:
    """Seconds of blank (white) frames at the start of a clip."""
    probe = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(src), "-vf", "negate,blackdetect=d=0.04:pix_th=0.08"]
        + ["-an", "-f", "null", "-"],
        capture_output=True,
        text=True,
    ).stderr
    m = re.search(r"black_start:0(?:\.0+)?\s+black_end:([\d.]+)", probe)
    return float(m.group(1)) if m else 0.0


def length(src: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if out and out != "N/A":
        return float(out)
    probe = subprocess.run(  # Playwright's webm may not carry a duration: count its frames
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0"]
        + ["-show_entries", "stream=nb_read_frames,r_frame_rate", "-of", "json", str(src)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    s = json.loads(probe)["streams"][0]
    num, den = map(int, s["r_frame_rate"].split("/"))
    return int(s["nb_read_frames"]) * den / num


SIGNON_SPEEDUP = 3.0
STEPS_SPEEDUP = 2.0  # replay steps: the 250 ms slow-motion and page loads are the dead time
# The console's holder field, as the corner label says it. While a run waits for help the lease
# is released and the holder is "nobody": the label says so rather than claim automation.
WHO = {"AWAITING_HUMAN": "No one", "HUMAN": "Person"}  # every other state: Automation


def control_timeline(run_dir: Path) -> list[tuple[float, str]]:
    """Who is in control, from the session controller's own transitions in the run log: the
    state and holder the console's banner shows. While a run waits for help nobody holds the
    lease ("No one"); while the operator holds it, "Person". After the hand-back, automation
    validates the screen and finishes; the lease field is briefly empty there and after the run
    completes, but automation is the actor, so those states read "Automation"."""
    out: list[tuple[float, str]] = []
    for e in events_of(run_dir):
        if e["kind"] == "control.transition":
            who = WHO.get(str(e.get("to")), "Automation")
            if not out or out[-1][1] != who:
                out.append((ts(e["ts"]), who))
    return out


def events_of(run_dir: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (run_dir / "run.jsonl").open()]


def anchor_of(events: Sequence[dict[str, Any]]) -> float:
    """The harness's first navigation: the moment the sign-on page is painted, which is where a
    clip's trimmed start lines up with the run log."""
    return next(
        ts(e["ts"]) for e in events if e["kind"] == "surface.act" and e.get("action") == "navigate"
    )


def play_replay(studio: "Studio", src: Path, run_dir: Path) -> None:
    """A replay clip from its first painted frame: the sign-on at 3x (dead time), then the
    capability's own steps at the recorded pace."""
    start, end = blank_until(studio.ffmpeg, src), length(src)
    events = events_of(run_dir)
    signed = next(ts(e["ts"]) for e in events if e["kind"] == "session.signed_on")
    cut = min(max(start, start + signed - anchor_of(events)), end)
    if cut - start > 0.2:
        studio.clip(src, start, cut, speed=SIGNON_SPEEDUP)
    studio.clip(src, cut, end, speed=STEPS_SPEEDUP)


def webm(folder: Path) -> Path:
    clips = sorted(folder.glob("*.webm"), key=lambda p: p.stat().st_mtime)
    expect(bool(clips), f"no clip in {folder}")
    return clips[0]


def meta(segment: str) -> dict[str, Any] | None:
    path = RAW / segment / "meta.json"
    return json.loads(path.read_text()) if path.is_file() else None


def ts(text: str) -> float:
    return datetime.fromisoformat(text).timestamp()


async def result_hold(
    studio: Studio,
    segment: str,
    src: Path,
    m: dict[str, Any],
    *,
    tone: str,
    corner: str | None = None,
) -> None:
    still = studio.last_frame(src)
    headline, detail = AFTER[segment], str(m.get("detail", ""))
    bar, bar_h = await studio.png(bar_html(headline, detail, tone, corner), transparent=True)
    zoom: Region | None = tuple(m["zoom"]) if m.get("zoom") else None  # type: ignore[assignment]
    # The headline and the system output are two pieces shown together: each gets its time.
    seconds = max(HOLD_S, read_s(headline), read_s(detail) if detail else 0.0)
    studio.hold(still, seconds, [bar], zoom, bar_h)


async def build_discovery(studio: Studio, m: dict[str, Any]) -> None:
    """Up to 4x, slowing only where the AI's logged rationale needs reading time. The clip is
    anchored on the harness's first navigation: the moment the sign-on page is painted."""
    src = webm(RAW / "discovery" / "video")
    start, end = blank_until(studio.ffmpeg, src), length(src)
    events = [json.loads(line) for line in (Path(m["run_dir"]) / "run.jsonl").open()]
    anchor = next(
        ts(e["ts"]) for e in events if e["kind"] == "surface.act" and e.get("action") == "navigate"
    )
    turns = [
        (start + ts(e["ts"]) - anchor, str(e.get("rationale") or ""))
        for e in events
        if e["kind"] == "discovery.turn"
    ]
    bounds = [(start, ""), *[(t, why) for t, why in turns if start < t < end]]
    for i, (t0, why) in enumerate(bounds):
        t1 = bounds[i + 1][0] if i + 1 < len(bounds) else end
        if t1 - t0 < 0.05:
            continue
        if not why:
            studio.clip(src, t0, t1, speed=SPEEDUP)
            continue
        need = read_s(f"AI: {why}")
        speed = min(SPEEDUP, max(1.0, (t1 - t0) / need))
        hold = max(0.0, need - (t1 - t0) / speed)
        sub, _ = await studio.png(subtitle_html(why), transparent=True)
        studio.clip(src, t0, t1, speed=speed, overlays=[(sub, 0, need)], hold=hold)
    await result_hold(studio, "discovery", src, m, tone=CLEARED)


async def build_replay(studio: Studio, m: dict[str, Any]) -> None:
    src = webm(RAW / "replay" / "video")
    play_replay(studio, src, Path(m["run_dir"]))
    await result_hold(studio, "replay", src, m, tone=CLEARED)
    live = studio.last_frame(src)
    beat, _ = await studio.png(side_by_side_html(live, Path(m["evidence_shot"]), REDACTION))
    studio.still(beat, max(5.0, read_s(REDACTION)))


async def build_missing(studio: Studio, m: dict[str, Any]) -> None:
    src = webm(RAW / "missing" / "video")
    play_replay(studio, src, Path(m["run_dir"]))
    await result_hold(studio, "missing", src, m, tone=AMBER)


async def build_gate(studio: Studio, m: dict[str, Any]) -> None:
    src = webm(RAW / "gate" / "video")
    play_replay(studio, src, Path(m["run_dir"]))
    await result_hold(studio, "gate", src, m, tone=STAMP)


async def build_handoff(studio: Studio, m: dict[str, Any]) -> None:
    """The console page: at 2x until the run is stuck (sign-on and search, already seen), then
    at the recorded pace while the person works, with who holds control in the corner."""
    src = webm(RAW / "handoff" / "console")
    start, end = blank_until(studio.ffmpeg, src), length(src)
    page0 = float(m["marks"]["page"])

    def at(wall: float) -> float:  # wall clock to the clip's own time
        return min(max(start, wall - page0), end)

    run_dir = Path(m["run_dir"]) if "run_dir" in m else next((RAW / "handoff" / "runs").glob("r_*"))
    timeline: list[tuple[float, str]] = [(start, "Automation")]
    for wall, who in control_timeline(run_dir):
        if who != timeline[-1][1]:
            timeline.append((at(wall), who))
    stuck = at(float(m["marks"]["stuck"]))
    automation, _ = await studio.png(corner_only_html("Automation"), transparent=True)
    if stuck - start > 0.2:
        studio.clip(src, start, stuck, speed=3.0, overlays=[(automation, 0, 999)])
    need = read_s(HANDOFF_MIDDLE)
    hold = max(0.0, need - (end - stuck))
    total = end - stuck + hold
    overlays: list[tuple[Path, float, float]] = []
    for i, (t, who) in enumerate(timeline):
        t1 = timeline[i + 1][0] if i + 1 < len(timeline) else end + hold
        if t1 <= stuck:
            continue
        label, _ = await studio.png(corner_only_html(who), transparent=True)
        overlays.append((label, max(0.0, t - stuck), t1 - stuck))
    middle, _ = await studio.png(bar_html(HANDOFF_MIDDLE), transparent=True)
    overlays.append((middle, 0.0, total))
    studio.clip(src, stuck, end, overlays=overlays, hold=hold)
    auto = webm(RAW / "handoff" / "video")
    await result_hold(studio, "handoff", auto, m, tone=CLEARED, corner=timeline[-1][1])


async def build_agent(studio: Studio, m: dict[str, Any]) -> None:
    src = webm(RAW / "agent" / "video")
    play_replay(studio, src, Path(m["run_dir"]))
    await result_hold(studio, "agent", src, m, tone=CLEARED)


BUILDERS = {
    "discovery": build_discovery,
    "replay": build_replay,
    "missing": build_missing,
    "gate": build_gate,
    "handoff": build_handoff,
    "agent": build_agent,
}


async def build(studio: Studio) -> None:
    await studio.card(OPENING[0])
    # The disclaimer is a footnote on the second card: two pieces shown together.
    await studio.card(OPENING[1], note=DISCLAIMER)
    for segment in SEGMENTS:
        await studio.card(BEFORE[segment], segment)
        m = meta(segment)
        if m is None:
            await studio.card(f"(Rehearsal: the {segment} segment isn't recorded yet.)", seconds=3)
            continue
        await BUILDERS[segment](studio, m)
    for text in CLOSING:
        await studio.card(text)


def concat(ffmpeg: str, parts: Sequence[Path], out: Path, work: Path) -> None:
    listing = work / "parts.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in parts))
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing)]
        + ["-c", "copy", "-movflags", "+faststart", str(out)],
        check=True,
    )


def contact_sheet(ffmpeg: str, film: Path, out: Path) -> None:
    """One frame every two seconds, eight across, for checking captions against footage."""
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", str(film), "-vf"]
        + ["fps=0.5,scale=400:-1,tile=8x14:padding=4:color=white", "-frames:v", "1", str(out)],
        check=True,
    )


async def assemble(ffmpeg: str) -> Path:
    complete = all(meta(s) is not None for s in SEGMENTS)
    out = FINAL if complete else RAW / "rehearsal.mp4"
    work = RAW / "_build"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            studio = Studio(browser, work, ffmpeg)
            await build(studio)
        finally:
            await browser.close()
    concat(ffmpeg, studio.parts, out, work)
    contact_sheet(ffmpeg, out, RAW / ("contact_sheet.png" if complete else "rehearsal_sheet.png"))
    shutil.rmtree(work, ignore_errors=True)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-video", action="store_true", help="record segments (opt-in)")
    parser.add_argument("--segments", default="", help="comma-separated segments to record")
    parser.add_argument("--offline", action="store_true", help="record the free segments only")
    parser.add_argument("--build", action="store_true", help="only assemble from the raw clips")
    args = parser.parse_args()
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None or shutil.which("ffprobe") is None:
        print("ffmpeg and ffprobe are needed (brew install ffmpeg).", file=sys.stderr)
        return 2
    if not args.build:
        if not args.record_video:
            print("Refusing to record without --record-video.", file=sys.stderr)
            return 2
        if args.offline:
            chosen = [s for s in SEGMENTS if s not in PAID]
        else:
            chosen = [s for s in args.segments.split(",") if s] or list(SEGMENTS)
        unknown = sorted(set(chosen) - set(SEGMENTS))
        if unknown:
            print(f"unknown segments: {unknown}", file=sys.stderr)
            return 2
        try:
            asyncio.run(record(chosen))
        except Mismatch as exc:
            print(f"recording stopped: {exc}", file=sys.stderr)
            return 1
    try:
        out = asyncio.run(assemble(ffmpeg))
    except Mismatch as exc:
        print(f"film not assembled: {exc}", file=sys.stderr)
        return 1
    print(f"{out.relative_to(REPO)}: {length(out):.0f}s, {out.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())

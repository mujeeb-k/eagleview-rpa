"""Records the demo video's footage and renders the narrated story, the one exception to
invariant 4 (docs/DECISIONS.md, "Demo video exception"). Recording needs --record-video, and only
ever films the synthetic mock bank.

Each segment records into its own raw folder under runs/demo_video/ (gitignored), with its run
log and a small meta.json, so the story can be re-edited without a new recording:
  discovery    the real model learns the lookup (one paid run: discover, compile, verify, probe)
  replay       the approved lookup for 10482, with no model
  missing      the same lookup for a member number that doesn't exist
  gate         the post flow, stopped at Post because nobody approves it
  handoff      an unknown alert, the operator console taking over (the scripted operator)
  copperhill   the same lookup at the second credit union, with its overlays
  ask_savings  an agent answering Amber's question through the catalog (one paid run)
  ask_missing  an agent asked about a member number that doesn't exist (one paid run)

    scripts/evidence/demo_video.sh --record-video --segments ask_savings,ask_missing
    scripts/evidence/demo_video.sh                      # re-render from the kept footage

After any recording, the story is built by video/prepare.py (which runs the sync check) and
rendered with Remotion to evidence/demo.mp4, with a poster frame at evidence/demo_poster.png.
Agent segments are filmed at real speed; the others are slowed like --headed for watchability."""

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
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

# Shortened to bring the film under 3:00, after the clips' dead time was cut.
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
    # Worded with care: an unapproved capability can still be replayed by a person at the
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
# Worded to match what the screenshot shows: balances stay readable on this training
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

    def tenant(self, slug: str = "lantern-bay") -> TenantProfile:
        return load_tenant(REPO / "tenants" / f"{slug}.yaml").model_copy(
            update={"base_url": self.banks[slug].url}
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
        slug: str = "lantern-bay",
    ) -> tuple[RunResult, list[TextBox], Path]:
        """A replay, filmed, with the text on its final screen read just before the page
        closes: the run's own geometry for the zoom. Same engine and opener as run_replay."""
        bank = self.banks[slug]
        bank.reset()
        if faults:
            bank.set_faults(**faults)
        policy, tenant = self.policy(), self.tenant(slug)
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


QUESTIONS = {
    "ask_savings": "Hi, I'm member 10482. How much is in my savings?",
    "ask_missing": "Can you check the savings for member 99999?",
}
REAL_SPEED = ("ask_savings", "ask_missing")  # filmed without slow motion: their timings are real


async def rec_ask(r: Recorder, segment: str) -> None:
    """An agent answering a member's question through the approved catalog: the real question,
    its real reply (verbatim, and without markdown for the screen), and the tool's replay."""
    folder = fresh(segment)
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
    question = QUESTIONS[segment]
    answer = await ask(
        question,
        client=make_client(r.env, Redactor()),
        model=model_from(r.env),
        meter=r.meter,
        catalog=catalog,
    )
    expect(bool(answer.tool_calls), "the agent called no tool")
    expect(bool(answer.answer) and "no answer" not in answer.answer, f"answer: {answer.answer!r}")
    save(
        folder,
        {
            "question": question,
            "answer": answer.answer,
            "answer_plain": plain(answer.answer),
            "agent_run": answer.run_id,
            "tool_calls": answer.tool_calls,
            "tools": [e.tool_name for e in entries],
            "run_dir": str(folder / "runs" / str(answer.tool_calls[0]["run_id"])),
        },
    )


async def rec_copperhill(r: Recorder) -> None:
    """The same approved lookup at the second credit union, with its reviewed overlays: the
    footage for the narrated story's reuse beat. Free: no model."""
    folder = fresh("copperhill")
    member = str(r.banks["copperhill"].tenant.demo_member)
    result, boxes, run_dir = await r.replay(
        folder, APPROVED_LOOKUP, {"member_id": member}, slug="copperhill"
    )
    expect(result.status == "success", f"Copperhill replay ended {result.status}")
    save(folder, {"zoom": table_region(boxes), "run_dir": str(run_dir), "member": member})


RECORDERS = {
    "discovery": rec_discovery,
    "replay": rec_replay,
    "missing": rec_missing,
    "gate": rec_gate,
    "handoff": rec_handoff,
    "copperhill": rec_copperhill,
    "ask_savings": lambda r: rec_ask(r, "ask_savings"),
    "ask_missing": lambda r: rec_ask(r, "ask_missing"),
}


async def record(segments: Sequence[str]) -> None:
    env = environment(REPO / ".env")
    meter = LedgerMeter(Ledger(REPO / "runs" / "discovery_ledger.jsonl"))
    async with async_playwright() as pw:
        # Headless, slowed like --headed so a viewer can follow each action; the agent segments
        # run at real speed, so the timings they show are the system's own.
        slowed = await pw.chromium.launch(headless=True, slow_mo=HEADED_SLOW_MO_MS)
        real = await pw.chromium.launch(headless=True)
        try:
            with running_banks(REPO / ".env") as banks:
                env |= {b.tenant.password_env: b.password for b in banks.values()}
                for segment in segments:
                    browser = real if segment in REAL_SPEED else slowed
                    print(f"recording {segment}", flush=True)
                    await RECORDERS[segment](Recorder(browser, banks, env, meter))
        finally:
            await slowed.close()
            await real.close()


def render() -> int:
    """The story from the kept footage: video/prepare.py (the sync check stops it on any
    mismatch), then Remotion renders evidence/demo.mp4 and the poster frame."""
    video, evidence = REPO / "video", REPO / "evidence"
    steps = [
        [sys.executable, str(video / "prepare.py")],
        ["npx", "remotion", "render", "src/index.ts", "Demo", str(evidence / "demo.mp4")],
        ["npx", "remotion", "still", "src/index.ts", "Poster", str(evidence / "demo_poster.png")],
    ]
    for step in steps:
        cwd = REPO if step[0] == sys.executable else video
        env = {**os.environ, "PYTHONPATH": str(REPO)}
        if subprocess.run(step, cwd=cwd, env=env).returncode != 0:
            print(f"demo video: {' '.join(step[:4])} failed", file=sys.stderr)
            return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-video", action="store_true", help="record segments (opt-in)")
    parser.add_argument("--segments", default="", help="comma-separated segments to record")
    args = parser.parse_args()
    if args.segments and not args.record_video:
        print("Refusing to record without --record-video.", file=sys.stderr)
        return 2
    if args.record_video:
        chosen = [s for s in args.segments.split(",") if s] or list(RECORDERS)
        unknown = sorted(set(chosen) - set(RECORDERS))
        if unknown:
            print(f"unknown segments: {unknown}", file=sys.stderr)
            return 2
        try:
            asyncio.run(record(chosen))
        except Mismatch as exc:
            print(f"recording stopped: {exc}", file=sys.stderr)
            return 1
    return render()


if __name__ == "__main__":
    sys.exit(main())

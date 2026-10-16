"""Prepares everything the Remotion story needs, from what is already on disk: the raw clips
and run logs under runs/demo_video/, the compiled recipe, the Copperhill overlays, and the
narration, spoken by a macOS voice.

    PYTHONPATH=. uv run python video/prepare.py

Writes video/public/media/ (gitignored): one mp4 per clip, a few stills, one narration file per
beat, and story.json, the timeline the composition reads. No model is called and nothing under
evidence/ is touched. Every coordinate, time and line of AI reasoning comes from a run log; the
chat's question and answer come from the real agent run."""

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from scripts.evidence.demo_video import APPROVED_LOOKUP, RAW
from video import sync
from video.sync import anchor_of, blank_until, control_timeline, events_of, length, ts

REPO = Path(__file__).resolve().parents[1]
OPEN_SHARE = REPO / "capabilities" / "drafts" / "member.open_share_account.yaml"
MEDIA = REPO / "video" / "public" / "media"
FPS = 30
VOICE = "Samantha"  # the most natural installed voice; no Premium or Enhanced voice is installed
RATE = 165  # words per minute
GAP = 0.2  # silence between subtitle cues
LEAD = 0.2  # silence before a scene's first cue
yaml = YAML(typ="safe")


def run(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def webm(segment: str, sub: str = "video") -> Path:
    clips = sorted((RAW / segment / sub).glob("*.webm"), key=lambda p: p.stat().st_mtime)
    if not clips:
        sys.exit(f"no raw clip for {segment}: record it with scripts/evidence/demo_video.sh")
    return clips[0]


def meta(segment: str) -> dict[str, Any]:
    return json.loads((RAW / segment / "meta.json").read_text())


# --- footage ---------------------------------------------------------------------------------


@dataclass
class Clip:
    """One raw clip and its run log, in the clip's own time (seconds from its first frame)."""

    name: str
    src: Path
    run_dir: Path
    start: float = 0.0
    duration: float = 0.0
    anchor: float = 0.0
    events: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    frames: "sync.Frames | None" = None

    def at(self, wall: float) -> float:
        """A run-log time on the clip's clock: the harness's first navigation is the moment the
        first page is painted, which is where the trimmed clip starts."""
        return self.start + wall - self.anchor

    def first(self, kind: str, **match: str) -> float:
        for e in self.events:
            if e["kind"] == kind and all(str(e.get(k)) == v for k, v in match.items()):
                return self.at(ts(e["ts"]))
        raise SystemExit(f"{self.name}: no {kind} {match}")


def clip(name: str, segment: str, run_dir: Path, sub: str = "video") -> Clip:
    src = webm(segment, sub)
    out = MEDIA / f"{name}.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf", f"fps={FPS},format=yuv420p"]
        + ["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-an", str(out)],
        check=True,
    )
    events = events_of(run_dir)
    c = Clip(name, out, run_dir, blank_until("ffmpeg", src), length(out), anchor_of(events), events)
    c.frames = sync.Frames.of(out)
    return c


def still(c: Clip, t: float, name: str) -> str:
    out = MEDIA / f"{name}.png"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(c.src)]
        + ["-frames:v", "1", str(out)],
        check=True,
    )
    return f"media/{out.name}"


TEXT_ACTIONS = ("fill", "type")


TYPED = {"login.enter_username": "the operator ID", "login.enter_password": "the password"}


def actions(c: Clip, values: dict[str, str], *, actors: tuple[str, ...]) -> list[dict[str, Any]]:
    """Every pointer and keyboard action, where it was aimed, and when it happened on screen.

    `done` is the run log's time, written as the action finished; `t` (and `pre`, the last frame
    before it) are measured from the footage by `sync.measure`. Typed text is shown as the
    declared input it was (a member number), or named ("the password"), never as bullets."""
    out: list[dict[str, Any]] = []
    last: list[int] | None = None
    for e in c.events:
        if e["kind"] != "surface.act" or e.get("status") != "done":
            continue
        actor = str(e.get("actor"))
        if not any(actor.startswith(a) for a in actors):
            continue
        kind = str(e.get("action"))
        point = e.get("point")
        if kind not in ("click", "double_click", "fill", "type", "select", "key"):
            continue
        if point is None or point == [0, 0]:
            point = last
        if point is None:
            continue
        last = point
        step = str(e.get("step_id") or "")
        target = e.get("target") or {}
        value = None
        if kind in TEXT_ACTIONS:
            value = TYPED.get(step) or values.get(step) or values.get("*")
        what = (
            target.get("name")
            or target.get("text")
            or step.replace("_", " ")
            or (f"the {target['role']}" if target.get("role") else target.get("tag"))
        )
        out.append(
            {
                "done": round(c.at(ts(e["ts"])), 3),
                "kind": kind,
                "x": point[0],
                "y": point[1],
                "value": value,
                "quoted": step not in TYPED,
                "actor": actor,
                "step": step,
                "name": f"{kind} {what}".strip(),
            }
        )
    assert c.frames is not None
    sync.measure(c.frames, out)
    for a in out:
        a["t"] = a["pre"] if a["pre"] is not None else a["done"]
    return out


def post_point() -> list[int]:
    """Where the Post button is: the recipe's own recorded position for its Post step (the run
    stops before clicking it, so there is no click in the log)."""
    cap = yaml.load(OPEN_SHARE.read_text())
    step = next(s for s in cap["steps"] if s.get("requires_approval"))
    loc = next(x for x in step["target"]["locators"] if x["strategy"] == "point")
    return [loc["x"], loc["y"]]


def modal_box(c: Clip, t: float) -> list[int]:
    """Where the unknown alert sits, from the footage: its dark title bar, the one dark run that
    starts inside the page and stops well short of its right edge (table headers span it)."""
    assert c.frames is not None
    frame = c.frames.full(t)
    w, h = frame.size
    data = frame.tobytes()  # one byte per pixel: the frames are greyscale
    bar: list[tuple[int, int, int]] = []
    for y in range(60, h // 2):
        row = data[y * w : (y + 1) * w]
        runs, x = [], 0
        while x < w:
            if row[x] < 70:
                x0 = x
                while x < w and row[x] < 70:
                    x += 1
                runs.append((x0, x - 1))
            x += 1
        for x0, x1 in runs:
            if 300 <= x1 - x0 <= 700 and x0 > 200 and x1 < w - 300:
                bar.append((y, x0, x1))
    if not bar:
        sys.exit("handoff: no alert title bar found in the footage")
    return [min(b[1] for b in bar), bar[0][0], max(b[2] for b in bar), bar[-1][0]]


def evidence_from_trace(c: Clip, acts: list[dict[str, Any]]) -> None:
    """Discovery's own record for each pointer action: its before/after screenshots and the
    grounded element's box, in order."""
    trace = [json.loads(line) for line in (c.run_dir / "trace.jsonl").open()]
    shot = [e for e in trace if (e.get("shots") or {}).get("before") and e.get("grounded")]
    for a, e in zip(acts, shot, strict=False):
        a["shots"] = [str(c.run_dir / e["shots"]["before"]), str(c.run_dir / e["shots"]["after"])]
        g = e["grounded"]
        a["bbox"] = g["bbox"]
        if not g.get("name") and g.get("anchor_left"):
            thing = "box" if g.get("role") == "textbox" else "button"
            a["name"] = f"{a['kind']} the {thing} next to '{g['anchor_left']}'"


def evidence_from_steps(c: Clip, acts: list[dict[str, Any]], cap_path: Path | None) -> None:
    """A replay's record for each step action: the step's before/after screenshots (numbered in
    step order) and, when the recipe applies unchanged, the position it recorded at discovery."""
    steps = [a for a in acts if a["step"] and not a["step"].startswith("login.")]
    pairs = sorted((c.run_dir / "steps").glob("*_before.png"))
    points: dict[str, tuple[int, int]] = {}
    if cap_path is not None:
        cap = yaml.load(cap_path.read_text())
        for s in cap["steps"]:
            for loc in (s.get("target") or {}).get("locators", []):
                if loc["strategy"] == "point":
                    points[s["id"]] = (loc["x"], loc["y"])
    if len(pairs) >= len(steps):
        for a, before in zip(steps, pairs, strict=False):
            a["shots"] = [str(before), str(before).replace("_before", "_after")]
    for a in steps:
        if a["step"] in points:
            a["expected"] = points[a["step"]]


def done_steps(c: Clip) -> list[dict[str, Any]]:
    return [
        {"t": round(c.at(ts(e["ts"])), 3), "step": e["step_id"]}
        for e in c.events
        if e["kind"] == "step.done" and not str(e.get("step_id", "")).startswith("login.")
    ]


def control(c: Clip, *, default: str) -> list[dict[str, Any]]:
    """Who drives the screen, from the controller's own transitions. A run without a hand-over
    has none, and is driven by `default` throughout."""
    names = {"Automation": "Recipe", "No one": "No one", "Person": "Person"}
    out = [{"t": 0.0, "who": default}]
    for wall, who in control_timeline(c.run_dir):
        out.append({"t": round(c.at(wall), 3), "who": names[who]})
    return out


def piece(c: Clip, a: float, b: float, *, cut: bool = False) -> dict[str, Any]:
    a, b = max(c.start, a), min(c.duration - 0.05, b)
    return {"clip": c.name, "from": round(a, 3), "to": round(b, 3), "cut": cut}


# --- narration -------------------------------------------------------------------------------


def speak(text: str, name: str) -> tuple[Path, float]:
    aiff = MEDIA / "vo" / f"{name}.aiff"
    wav = MEDIA / "vo" / f"{name}.wav"
    aiff.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["say", "-v", VOICE, "-r", str(RATE), "-o", str(aiff), text], check=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(aiff),
            "-ar",
            "48000",
            "-ac",
            "1",
            str(wav),
        ],
        check=True,
    )
    aiff.unlink()
    return wav, length(wav)


def silence(seconds: float, name: str) -> Path:
    out = MEDIA / "vo" / f"{name}.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono"]
        + ["-t", f"{seconds:.3f}", str(out)],
        check=True,
    )
    return out


def concat(parts: list[Path], out: Path) -> None:
    listing = out.with_suffix(".txt")
    listing.write_text("".join(f"file '{p}'\n" for p in parts))
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing)]
        + ["-c", "copy", str(out)],
        check=True,
    )
    listing.unlink()


# --- the recipe and its adjustments ----------------------------------------------------------


def how_found(step: dict[str, Any]) -> str:
    """The recipe's first way of finding a step's target, in plain words."""
    if step["action"]["type"] == "extract":
        (spec,) = step["extract"].values()
        return f"the table under '{spec['table']['anchor_text']}'"
    loc = next(x for x in step["target"]["locators"] if x["strategy"] != "point")
    if loc["strategy"] == "a11y":
        return f"the {loc['role']} '{loc['name']}'"
    if loc["strategy"] == "anchor":
        thing = {"textbox": "box", "image": "button", "select": "list"}.get(loc["control"], "item")
        where = {"right_of": "next to", "below": "under", "left_of": "before"}.get(
            loc["relation"], "by"
        )
        return f"the {thing} {where} '{loc['anchor_text'][0]}'"
    return f"by its {loc['strategy']}"


TITLES = {
    "click": "Click",
    "fill": "Type",
    "extract": "Read",
}


def recipe(discovery_actions: list[dict[str, Any]], table: list[int]) -> list[dict[str, Any]]:
    """The four steps the video's own discovery run compiled into, each with where on screen it
    happened (for the lift-off) and how the recipe will find it again."""
    path = next((RAW / "discovery" / "artifact").glob("*.yaml"))
    cap = yaml.load(path.read_text())
    titles = [
        "Open Member Inquiry",
        "Type the member number",
        "Click GO",
        "Read the Share Accounts table",
    ]
    # Where each step happened in the discovery footage: the first click (the menu link), the
    # typing (the field), the click after the typing (GO), and the table it read.
    clicks = [a for a in discovery_actions if a["kind"] == "click"]
    typed = next(a for a in discovery_actions if a["kind"] in ("type", "fill"))
    go = next(a for a in clicks if a["t"] > typed["t"])
    x0, y0, x1, y1 = table
    where = [clicks[0], typed, go, {"x": (x0 + x1) // 2, "y": (y0 + y1) // 2}]
    steps = [
        {
            "id": s["id"],
            "title": titles[i],
            "how": how_found(s),
            "x": where[i]["x"],
            "y": where[i]["y"],
        }
        for i, s in enumerate(cap["steps"])
    ]
    if len(steps) != 4:
        sys.exit(f"the discovery recipe has {len(steps)} steps; the narration says four")
    return steps


def adjustments() -> list[dict[str, str]]:
    """Copperhill's reviewed overlays, as differences pinned to the recipe: a renamed label on
    the step that finds things by it, a different search button, and an extra screen."""
    out: list[dict[str, str]] = []
    for path in sorted((REPO / "overlays" / "copperhill").glob("*.yaml")):
        o = yaml.load(path.read_text())
        for label, aliases in (o.get("label_aliases") or {}).items():
            step = {"Member #": "enter_member_id", "Member Inquiry": "open_member_inquiry"}
            out.append({"step": step.get(label, ""), "text": f"“{label}” → “{aliases[0]}”"})
        for state in o.get("global_states_add") or []:
            if "Branch" in json.dumps(state):
                out.append({"step": "", "text": "+ a Select Branch screen after sign-on"})
        for step_id, patch in (o.get("step_patches") or {}).items():
            names = [x.get("name") for x in patch["target"]["locators"]["prepend"] if x.get("name")]
            if step_id == "submit_member_search" and names:
                out.append({"step": step_id, "text": f"GO → “{names[0]}” button"})
    return out


def first_sentence(text: str) -> str:
    """The reply's first sentence, verbatim: in both replies it is the one that answers."""
    head = text.strip().split("\n", 1)[0]
    m = re.match(r"(.+?[.!?])(\s|$)", head)
    return m.group(1) if m else head


def measured(discovery_run: str, agent: "Clip") -> dict[str, float]:
    """The two numbers on screen, from the ledger and the run logs only: what the discovery run
    cost, and how long the agent's replay took with no model calls during it."""
    ledger = [json.loads(line) for line in (REPO / "runs" / "discovery_ledger.jsonl").open()]
    usd = sum(float(e["usd"]) for e in ledger if e["run_id"] == discovery_run)
    events = events_of(agent.run_dir)
    start = ts(next(e["ts"] for e in events if e["kind"] == "run.start"))
    end = next(e for e in events if e["kind"] == "run.end")
    during = [e for e in ledger if start <= ts(e["ts"]) <= ts(end["ts"])]
    if during:
        sys.exit(f"the replay overlapped {len(during)} model calls; '0 AI calls' would be false")
    return {"discovery_usd": usd, "replay_s": end["timing_ms"]["total"] / 1000}


# --- the story -------------------------------------------------------------------------------


@dataclass
class Scene:
    """One stretch of the film: what it shows (`kind` and its props), what is said (cues, in
    order), and any footage, played at its recorded speed and then held on its last frame for
    as long as the narration still needs."""

    id: str
    kind: str
    cues: list[str]
    pieces: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    props: dict[str, Any] = field(default_factory=dict[str, Any])
    min_s: float = 0.0
    tail: float = 0.6
    after_cue: int | None = None  # footage starts when this cue ends (None: at the scene start)
    # The camera's zoom onto a region: (region name, cue index, lead). It starts `lead` seconds
    # before that cue, but never before the last action on screen has finished: the camera
    # waits for the footage, so it never crops out a click or frames a screen not yet shown.
    zoom: tuple[str, int, float] | None = None


def build() -> dict[str, Any]:
    shutil.rmtree(MEDIA, ignore_errors=True)
    MEDIA.mkdir(parents=True)

    m_disc, m_agent, m_hand = meta("discovery"), meta("ask_savings"), meta("handoff")
    m_ask2 = meta("ask_missing")
    m_miss, m_gate, m_rep, m_cop = meta("missing"), meta("gate"), meta("replay"), meta("copperhill")
    disc = clip("discovery", "discovery", Path(m_disc["run_dir"]))
    agent = clip("agent", "ask_savings", Path(m_agent["run_dir"]))
    hand_dir = m_hand.get("run_dir") or str(next((RAW / "handoff" / "runs").glob("r_*")))
    hand = clip("handoff", "handoff", Path(hand_dir))
    miss = clip("missing", "missing", Path(m_miss["run_dir"]))
    gate = clip("gate", "gate", Path(m_gate["run_dir"]))
    rep = clip("replay", "replay", Path(m_rep["run_dir"]))
    cop = clip("copperhill", "copperhill", Path(m_cop["run_dir"]))
    clips = {c.name: c for c in (disc, agent, hand, miss, gate, rep, cop)}

    question, answer = m_agent["question"], m_agent["answer_plain"]
    numbers = measured(Path(m_disc["run_dir"]).name, agent)

    # Discovery: jump cuts over the model's thinking. Each kept piece runs from just before a
    # turn's first action to just after its last one has painted.
    d_actions = actions(disc, {"*": "10482"}, actors=("llm",))
    turns = [
        {"t": round(disc.at(ts(e["ts"])), 3), "text": str(e.get("rationale") or "")}
        for e in disc.events
        if e["kind"] == "discovery.turn"
    ]
    # Each kept piece runs from shortly before the turn's first action (room for its thought)
    # to just after its last action's effect has painted; the thinking between is cut.
    d_pieces = []
    prev = disc.start
    for i, turn in enumerate(turns):
        nxt = turns[i + 1]["t"] if i + 1 < len(turns) else disc.duration
        acts = [a for a in d_actions if turn["t"] <= a["done"] < nxt]
        first = min((a["t"] for a in acts), default=turn["t"])
        a = max(prev, min(turn["t"], first) - 0.7)
        b = (max(a2["onset"] or a2["done"] for a2 in acts) + 0.6) if acts else turn["t"] + 1.6
        d_pieces.append(piece(disc, a, min(b, nxt + 0.6), cut=True))
        prev = d_pieces[-1]["to"]
    evidence_from_trace(disc, d_actions)

    # The handoff: the recipe signs on and searches; the unknown alert; the long wait before
    # it asks for help is cut; then the person; then the recipe again.
    h_actions = actions(
        hand, {"enter_member_id": "10482"}, actors=("harness", "automation", "operator")
    )
    signed = hand.first("session.signed_on")
    go = next(a["t"] for a in h_actions if a["step"] == "submit_member_search")
    ops = [a for a in h_actions if a["actor"].startswith("operator")]
    waiting = hand.first("control.transition", to="AWAITING_HUMAN")
    modal = modal_box(hand, waiting - 0.5)
    human = hand.first("control.transition", to="HUMAN")
    resume = hand.first("control.transition", to="RESUME_REQUESTED")

    def replay_actions(c: Clip, member: str, cap: Path | None) -> list[dict[str, Any]]:
        acts = actions(c, {"enter_member_id": member}, actors=("harness", "automation"))
        evidence_from_steps(c, acts, cap)
        return acts

    evidence_from_steps(hand, h_actions, APPROVED_LOOKUP)
    a_agent = replay_actions(agent, "10482", APPROVED_LOOKUP)
    a_miss = replay_actions(miss, "99999", APPROVED_LOOKUP)
    a_gate = replay_actions(gate, "10482", OPEN_SHARE)
    a_cop = replay_actions(cop, str(m_cop["member"]), None)  # Copperhill's overlay moves things

    regions = {
        "table": m_rep["zoom"],
        "banner": m_miss["zoom"],
        "post": m_gate["zoom"],
        "detail": m_hand["zoom"],
        "copperhill_table": m_cop["zoom"],
    }

    scenes = [
        Scene(
            "hook",
            "hook",
            [
                "Amber asks her credit union's AI assistant a simple question:",
                "how much is in my savings?",
            ],
            props={"question": question, "who": "Amber"},
            min_s=6.0,
        ),
        Scene(
            "problem",
            "problem",
            [
                "The answer lives inside the credit union's teller software.",
                "It's old, and it has no way for other programs to plug in.",
                "The only way in is a person clicking through its screens.",
            ],
            props={
                "question": question,
                "app": still(
                    rep, rep.first("step.done", step_id="open_member_inquiry") + 0.3, "app_search"
                ),
                "screens": [
                    still(rep, rep.start + 0.4, "screen_signon"),
                    still(
                        rep,
                        rep.first("step.done", step_id="open_member_inquiry") + 0.3,
                        "screen_search",
                    ),
                    still(rep, rep.duration - 0.1, "screen_detail"),
                ],
            },
        ),
        Scene(
            "learn",
            "discovery",
            [
                "Eagleview lets an AI learn the task once, the way a new teller would.",
                "It looks at the screen, decides what to do, and clicks.",
            ],
            pieces=d_pieces,
            props={
                "actions": d_actions,
                "turns": turns,
                "control": [{"t": 0, "who": "AI"}],
                "metric": f"1 AI run · ${numbers['discovery_usd']:.2f}",
            },
        ),
        Scene(
            "recipe",
            "recipe",
            [
                "Eagleview writes down what worked as a recipe: four steps,",
                "and for each one, how to find it again by its label,",
                "not just by where it was on screen.",
            ],
            props={
                "steps": recipe(d_actions, regions["table"]),
                "still": still(disc, disc.duration - 0.1, "disc_end"),
            },
            min_s=9,
        ),
        Scene(
            "approve",
            "approve",
            ["A staff member checks the recipe and approves it."],
            props={"steps": recipe(d_actions, regions["table"])},
            min_s=3.5,
        ),
        Scene(
            "answer",
            "answer",
            [
                "Now when Amber asks, no AI touches the screen.",
                "Eagleview follows the approved recipe,",
                "checks each screen is right before moving on,",
                "and hands back her balances.",
            ],
            pieces=[piece(agent, agent.first("session.signed_on"), agent.duration)],
            zoom=("table", 2, 0.3),
            props={
                "question": question,
                "answer": answer,
                "highlight": first_sentence(answer),
                "status": "Looking up balances for member 10482",
                "metric": f"0 AI calls · {numbers['replay_s']:.1f} s",
                "actions": a_agent,
                "done": done_steps(agent),
                "control": [{"t": 0, "who": "Recipe"}],
                "region": regions["table"],
            },
            tail=3.5,
        ),
        Scene(
            "surprise",
            "handoff",
            ["Real systems throw surprises, like this warning the recipe has never seen."],
            pieces=[piece(hand, signed, go + 1.6)],
            props={
                "phase": "alert",
                "actions": h_actions,
                "control": control(hand, default="Recipe"),
                "modal_t": go + 0.3,
                "modal": modal,
            },
        ),
        Scene(
            "pause",
            "handoff",
            ["Eagleview doesn't guess. It pauses and calls a staff member,"],
            pieces=[piece(hand, waiting - 0.4, human, cut=True)],
            props={
                "phase": "waiting",
                "actions": h_actions,
                "control": control(hand, default="Recipe"),
                "modal_t": go + 0.3,
                "modal": modal,
            },
        ),
        Scene(
            "takeover",
            "handoff",
            ["who takes over the very same screen, clears the warning,"],
            pieces=[
                piece(hand, max(human, ops[0]["onset"] - 0.9), ops[0]["onset"] + 0.5),
                piece(hand, ops[1]["onset"] - 0.7, min(resume, ops[1]["onset"] + 0.6)),
            ],
            props={
                "phase": "person",
                "actions": h_actions,
                "control": control(hand, default="Recipe"),
                "modal_t": go + 0.3,
                "modal": modal,
            },
        ),
        Scene(
            "handback",
            "handoff",
            ["and hands control back. The recipe finishes the job."],
            pieces=[piece(hand, resume, hand.duration)],
            zoom=("detail", 0, 0.0),
            props={
                "phase": "back",
                "actions": h_actions,
                "control": control(hand, default="Recipe"),
                "modal_t": go + 0.3,
                "modal": modal,
                "region": regions["detail"],
            },
        ),
        Scene(
            "missing",
            "missing",
            [
                "If someone asks about a member number that doesn't exist,",
                "the bank says so, and the assistant passes on a clear answer instead of an error.",
            ],
            pieces=[piece(miss, miss.first("session.signed_on"), miss.duration)],
            zoom=("banner", 1, 0.4),
            props={
                "actions": a_miss,
                "control": [{"t": 0, "who": "Recipe"}],
                "region": regions["banner"],
                "question": m_ask2["question"],
                "answer": m_ask2["answer_plain"],
                "highlight": first_sentence(m_ask2["answer_plain"]),
            },
            tail=2.0,
        ),
        Scene(
            "gate",
            "gate",
            [
                "And anything that can't be undone, like opening a new account,",
                "never happens on its own.",
                "Eagleview stops at the final button until a person approves.",
            ],
            pieces=[piece(gate, gate.first("session.signed_on"), gate.duration)],
            zoom=("post", 1, 0.3),
            props={
                "actions": a_gate,
                "control": [{"t": 0, "who": "Recipe"}],
                "region": regions["post"],
                "post": post_point(),
            },
            tail=3.0,
        ),
        Scene(
            "privacy",
            "privacy",
            ["Anything Eagleview saves has names and contact details blacked out."],
            props={
                "live": still(rep, rep.duration - 0.1, "live"),
                "saved": copy_image(Path(m_rep["evidence_shot"]), "saved"),
            },
            min_s=4.5,
        ),
        Scene(
            "reuse",
            "reuse",
            [
                "And because many credit unions run this same software,",
                "a recipe learned at one works at another with a few small adjustments.",
            ],
            pieces=[piece(cop, cop.first("session.signed_on"), cop.duration)],
            after_cue=0,
            tail=0.4,
            props={
                "diffs": adjustments(),
                "steps": recipe(d_actions, regions["table"]),
                "actions": a_cop,
                "control": [{"t": 0, "who": "Recipe"}],
            },
        ),
        Scene(
            "close",
            "close",
            [
                "Learned once by an AI. Approved by a person.",
                "Run safely, the same way, every time.",
            ],
            min_s=5,
        ),
        Scene("url", "url", [], props={"url": "github.com/mujeeb-k/eagleview-rpa"}, min_s=3.1),
    ]
    return lay_out(scenes, clips, regions)


def copy_image(src: Path, name: str) -> str:
    out = MEDIA / f"{name}.png"
    shutil.copyfile(src, out)
    return f"media/{out.name}"


def lay_out(scenes: list[Scene], clips: dict[str, Clip], regions: dict[str, Any]) -> dict[str, Any]:
    """Times every scene from its narration and its footage, and writes one audio file per
    beat (a beat is a run of scenes sharing a narration file)."""
    out_scenes: list[dict[str, Any]] = []
    cues_out: list[dict[str, Any]] = []
    audio: list[dict[str, Any]] = []
    t = 0.0
    for n, s in enumerate(scenes):
        spoken: list[tuple[Path, float]] = [
            speak(text, f"{s.id}_{i}") for i, text in enumerate(s.cues)
        ]
        talk = (LEAD + sum(d for _, d in spoken) + GAP * max(0, len(spoken) - 1)) if spoken else 0.0
        delay = 0.0
        if s.after_cue is not None:
            delay = LEAD + sum(d for _, d in spoken[: s.after_cue + 1]) + GAP * (s.after_cue + 1)
        acts: list[dict[str, Any]] = s.props.get("actions", [])
        segments, presses = sync.schedule(s.pieces, acts, delay)
        footage = (segments[-1]["o1"] - delay) if segments else 0.0
        duration = max(talk + 0.5, delay + footage + s.tail if s.pieces else 0.0, s.min_s)
        thoughts: list[dict[str, Any]] = []
        if "turns" in s.props:
            turns = [
                {**turn, "out": sync.out_time(segments, turn["t"])} for turn in s.props["turns"]
            ]
            thoughts = sync.thoughts(turns, acts, presses, duration)
        parts: list[Path] = []
        at = LEAD if spoken else 0.0
        if spoken:
            parts.append(silence(LEAD, f"{s.id}_lead"))
        for i, ((wav, d), text) in enumerate(zip(spoken, s.cues, strict=True)):
            cues_out.append(
                {
                    "text": text,
                    "start": round(t + at, 3),
                    "end": round(t + at + d, 3),
                    "scene": s.id,
                    "index": i,
                }
            )
            parts.append(wav)
            at += d
            if i + 1 < len(spoken):
                parts.append(silence(GAP, f"{s.id}_gap{i}"))
                at += GAP
        parts.append(silence(max(0.05, duration - at), f"{s.id}_tail"))
        beat = MEDIA / f"vo_{n:02d}_{s.id}.wav"
        concat(parts, beat)
        for p in parts:
            p.unlink(missing_ok=True)
        audio.append(
            {"src": f"media/{beat.name}", "start": round(t, 3), "duration": round(duration, 3)}
        )
        local = [c for c in cues_out if c["scene"] == s.id]
        shots: list[dict[str, Any]] = []
        if s.zoom is not None:
            name, cue, lead = s.zoom
            after = max((p["o1"] for p in presses), default=0.0) + 0.3
            shots.append(
                {"t": round(max(local[cue]["start"] - t - lead, after), 3), "region": regions[name]}
            )
        out_scenes.append(
            {
                "id": s.id,
                "kind": s.kind,
                "start": round(t, 3),
                "duration": round(duration, 3),
                "pieces": s.pieces,
                "segments": segments,
                "presses": presses,
                "thoughts": thoughts,
                "shots": shots,
                "delay": round(delay, 3),
                "props": s.props,
                "cues": [
                    {"start": round(c["start"] - t, 3), "end": round(c["end"] - t, 3)}
                    for c in local
                ],
            }
        )
        t += duration
    shutil.rmtree(MEDIA / "vo", ignore_errors=True)
    checked = check_sync(out_scenes, clips)
    return {
        "sync": checked,
        "fps": FPS,
        "voice": VOICE,
        "rate": RATE,
        "duration": round(t, 3),
        "scenes": out_scenes,
        "cues": cues_out,
        "audio": audio,
        "clips": {
            k: {
                "src": f"media/{c.src.name}",
                "duration": round(c.duration, 3),
                "width": 1280,
                "height": 800,
            }
            for k, c in clips.items()
        },
        "regions": regions,
    }


def check_sync(scenes: list[dict[str, Any]], clips: dict[str, Clip]) -> list[dict[str, Any]]:
    """Every overlaid action, checked against its own footage (see video/sync.py). The build
    stops here if any of them doesn't match."""
    out: list[dict[str, Any]] = []
    failed = 0
    for sc in scenes:
        if not sc["presses"]:
            continue
        clip = clips[sc["pieces"][0]["clip"]]
        assert clip.frames is not None
        acts = sc["props"]["actions"]
        pressed = {p["action"] for p in sc["presses"]}
        for i, a in enumerate(acts):
            inside = any(p["from"] <= a["done"] < p["to"] for p in sc["pieces"])
            if inside and i not in pressed:
                failed += 1
                out.append(
                    {
                        "scene": sc["id"],
                        "name": a["name"],
                        "t": None,
                        "evidence": "-",
                        "problems": ["shown in the footage but no moment measured, so no ripple"],
                    }
                )
        for press in sc["presses"]:
            a = acts[press["action"]]
            shots = (Path(a["shots"][0]), Path(a["shots"][1])) if a.get("shots") else None
            expected = tuple(a["expected"]) if a.get("expected") else None
            problems = sync.check_action(
                clip.frames,
                a,
                shots=shots,
                bbox=a.get("bbox"),
                expected=expected,  # type: ignore[arg-type]
            )
            left, top, scale = sync.camera(sc["shots"], press["o0"])
            view_w, view_h = sync.SRC[0] / scale, sync.SRC[1] / scale
            if not (left <= a["x"] <= left + view_w and top <= a["y"] <= top + view_h):
                problems.append(f"the ripple ({a['x']},{a['y']}) is outside the camera's view")
            shown = sync.source_at(sc["segments"], press["o0"] + sync.HOLD / 2)
            if abs(shown - a["pre"]) > 1 / FPS:
                problems.append(
                    f"the hold shows source {shown:.3f}s, not the pre-action {a['pre']:.3f}s"
                )
            failed += bool(problems)
            out.append(
                {
                    "scene": sc["id"],
                    "name": a["name"],
                    "t": round(sc["start"] + press["o0"] + sync.HOLD * 0.6, 3),
                    "evidence": "screenshots" if shots else "footage",
                    "problems": problems,
                }
            )
    for r in out:
        mark = "ok " if not r["problems"] else "BAD"
        print(
            f"  {mark} {r['scene']:<9} {r['name']:<34} ({r['evidence']}) {'; '.join(r['problems'])}"
        )
    if failed:
        sys.exit(f"sync check failed for {failed} of {len(out)} actions")
    print(f"sync check: all {len(out)} overlaid actions match their footage")
    return out


def main() -> int:
    story = build()
    (MEDIA / "story.json").write_text(json.dumps(story, indent=2, ensure_ascii=False) + "\n")
    words = sum(len(c["text"].split()) for c in story["cues"])
    print(f"story.json: {len(story['scenes'])} scenes, {story['duration']:.1f}s, {words} words")
    return 0


if __name__ == "__main__":
    sys.exit(main())

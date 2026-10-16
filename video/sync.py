"""When each overlaid action happens on screen, and a check that the overlays agree with it.

The run log's `surface.act` time is written as an action finishes, and the mock bank paints the
next screen within a frame or two of a click. So an overlay drawn from that time onwards plays
on the screen the action produced. Instead, each action's moment is measured from the footage:
the first frame where its effect appears (the field takes focus, the digits appear, the next
screen paints), searched for around its logged time. The footage then holds the last frame
before that effect for a moment, and the ripple plays on it. Footage and overlays both read the
one timeline built here, so trims, jump cuts and holds apply to both alike; the camera's zoom
and pan already apply to both, because the overlays are drawn inside the footage's transform.

`check()` fails the build unless, for every overlaid action, the held frame is the screen before
the action: stable, followed by the action's effect, closer to the action's own logged
"before" screenshot than to its "after" one (where those exist), and with the target element,
not blank space, under the ripple."""

import json
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageStat

FPS = 30
HOLD = 0.5  # seconds the pre-action frame is held while the ripple plays
SMALL = (320, 200)
SRC = (1280, 800)


@dataclass
class Frames:
    """A clip decoded once: small greyscale frames for change detection, full frames on demand."""

    src: Path
    small: list[Image.Image] = field(default_factory=list[Image.Image])
    _full: dict[int, Image.Image] = field(default_factory=dict[int, Image.Image])

    @classmethod
    def of(cls, src: Path) -> "Frames":
        w, h = SMALL
        raw = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-i", str(src), "-vf", f"scale={w}:{h},format=gray"]
            + ["-f", "rawvideo", "-"],
            capture_output=True,
            check=True,
        ).stdout
        frames = [
            Image.frombytes("L", SMALL, raw[i : i + w * h]) for i in range(0, len(raw), w * h)
        ]
        return cls(src, frames)

    def index(self, t: float) -> int:
        return min(len(self.small) - 1, max(0, round(t * FPS)))

    def full(self, t: float) -> Image.Image:
        i = self.index(t)
        if i not in self._full:
            raw = subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-i", str(self.src), "-vf"]
                + [f"select=eq(n\\,{i}),format=gray", "-frames:v", "1", "-f", "rawvideo", "-"],
                capture_output=True,
                check=True,
            ).stdout
            self._full[i] = Image.frombytes("L", SRC, raw[: SRC[0] * SRC[1]])
        return self._full[i]


def mean_diff(a: Image.Image, b: Image.Image) -> float:
    return float(ImageStat.Stat(ImageChops.difference(a, b)).mean[0])


def local_box(x: int, y: int) -> tuple[int, int, int, int]:
    """The neighbourhood of a point, in the small frames: about 160x56 screen pixels."""
    sx, sy = SMALL[0] / SRC[0], SMALL[1] / SRC[1]
    cx, cy = round(x * sx), round(y * sy)
    return (max(0, cx - 20), max(0, cy - 7), min(SMALL[0], cx + 20), min(SMALL[1], cy + 7))


def tight_box(x: int, y: int) -> tuple[int, int, int, int]:
    """A small control's own pixels (a checkbox, a button): about 40x24 screen pixels."""
    sx, sy = SMALL[0] / SRC[0], SMALL[1] / SRC[1]
    cx, cy = round(x * sx), round(y * sy)
    return (max(0, cx - 5), max(0, cy - 3), min(SMALL[0], cx + 5), min(SMALL[1], cy + 3))


def changed(
    f: Frames, i: int, box: tuple[int, int, int, int], tight: tuple[int, int, int, int]
) -> bool:
    if i <= 0:
        return False
    a, b = f.small[i - 1], f.small[i]
    return (
        mean_diff(a.crop(tight), b.crop(tight)) > 6.0
        or mean_diff(a.crop(box), b.crop(box)) > 4.0
        or mean_diff(a, b) > 1.5
    )


def settled(f: Frames, t: float, frames: int = 4) -> float:
    """The first time from t on when the screen has stopped changing for `frames` frames: an
    earlier action's new page may take several frames to paint."""
    i, still = f.index(t), 0
    while i + 1 < len(f.small) and still < frames:
        i += 1
        still = still + 1 if mean_diff(f.small[i - 1], f.small[i]) < 0.6 else 0
    return (i - frames) / FPS


def measure(f: Frames, actions: list[dict[str, Any]]) -> None:
    """Sets each action's `onset` (first frame of its effect) and `pre` (the last frame before
    it), searching from shortly before its logged time, and never before the previous action."""
    floor = 0.0
    for a in actions:
        box, tight = local_box(a["x"], a["y"]), tight_box(a["x"], a["y"])
        lo = max(floor, a["done"] - 0.7)
        hi = a["done"] + 0.25
        onset = None
        for i in range(f.index(lo) + 1, f.index(hi) + 1):
            if changed(f, i, box, tight):
                onset = i / FPS
                break
        a["onset"] = onset
        a["pre"] = None if onset is None else round(onset - 1 / FPS, 4)
        if onset is not None:
            floor = settled(f, onset)


# --- the timeline ------------------------------------------------------------------------------


def schedule(
    pieces: Sequence[dict[str, Any]], actions: Sequence[dict[str, Any]], delay: float
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The scene's segments in output time, with a hold on each action's pre-action frame, and
    each shown action's press window. Segments: {kind: play, from, to, o0, o1, cut} or
    {kind: hold, at, o0, o1, action}."""
    segments: list[dict[str, Any]] = []
    presses: list[dict[str, Any]] = []
    o = delay
    for p in pieces:
        start, first = p["from"], True
        holds = sorted(
            (i, a)
            for i, a in enumerate(actions)
            if a.get("pre") is not None and p["from"] <= a["pre"] < p["to"]
        )
        holds.sort(key=lambda x: x[1]["pre"])
        for i, a in holds:
            if a["pre"] > start:
                segments.append(
                    {
                        "kind": "play",
                        "from": start,
                        "to": a["pre"],
                        "o0": o,
                        "o1": o + a["pre"] - start,
                        "cut": p["cut"] and first,
                    }
                )
                o += a["pre"] - start
                first = False
            segments.append({"kind": "hold", "at": a["pre"], "o0": o, "o1": o + HOLD, "action": i})
            presses.append({"action": i, "o0": round(o, 4), "o1": round(o + HOLD, 4)})
            o += HOLD
            start = a["pre"]
        if p["to"] > start:
            segments.append(
                {
                    "kind": "play",
                    "from": start,
                    "to": p["to"],
                    "o0": o,
                    "o1": o + p["to"] - start,
                    "cut": p["cut"] and first,
                }
            )
            o += p["to"] - start
    for s in segments:
        s["o0"], s["o1"] = round(s["o0"], 4), round(s["o1"], 4)
    return segments, presses


def source_at(segments: Sequence[dict[str, Any]], t: float) -> float:
    """The source time on screen at scene time t: the same rule the composition uses."""
    if not segments:
        return 0.0
    if t < segments[0]["o0"]:
        first = segments[0]
        return first["from"] if first["kind"] == "play" else first["at"]
    for s in segments:
        if s["o0"] <= t < s["o1"]:
            return s["at"] if s["kind"] == "hold" else s["from"] + (t - s["o0"])
    last = segments[-1]
    return last["at"] if last["kind"] == "hold" else last["to"]


def out_time(segments: Sequence[dict[str, Any]], src: float) -> float:
    """The scene time at which source time `src` is first on screen."""
    for s in segments:
        if s["kind"] == "play" and s["from"] <= src < s["to"]:
            return s["o0"] + (src - s["from"])
        if s["kind"] == "play" and src < s["from"]:
            return s["o0"]
    return segments[-1]["o1"] if segments else 0.0


def camera(shots: Sequence[dict[str, Any]], t: float) -> tuple[float, float, float]:
    """The camera's visible window (left, top, zoom) at scene time t: the same rule as the
    composition's camera() in src/Footage.tsx, so the check sees what the viewer sees."""

    def view(r: Sequence[int] | None) -> tuple[float, float, float]:
        if r is None:
            return (0.0, 0.0, 1.0)
        x0, y0, x1, y1 = r
        pad = 40
        scale = min(SRC[0] / (x1 - x0 + 2 * pad), SRC[1] / (y1 - y0 + 2 * pad), 2.4)
        w, h = SRC[0] / scale, SRC[1] / scale
        left = min(max(0.0, (x0 + x1) / 2 - w / 2), SRC[0] - w)
        top = min(max(0.0, (y0 + y1) / 2 - h / 2), SRC[1] - h)
        return (left, top, scale)

    frm = to = view(None)
    t0 = 0.0
    for shot in shots:
        if shot["t"] > t:
            break
        frm, to, t0 = to, view(shot["region"]), shot["t"]
    x = min(1.0, max(0.0, (t - t0) / 0.9))
    p = x * x * (3 - 2 * x)  # close to the composition's ease; used only for a containment check
    return tuple(a + (b - a) * p for a, b in zip(frm, to, strict=True))  # type: ignore[return-value]


def thoughts(
    turns: Sequence[dict[str, Any]],
    actions: Sequence[dict[str, Any]],
    presses: Sequence[dict[str, Any]],
    end: float,
) -> list[dict[str, Any]]:
    """Each line of the AI's reasoning belongs to the first action of its batch: it appears just
    before that action and is gone before the next action, whichever turn that belongs to."""
    by_action = {p["action"]: p for p in presses}
    order = sorted(by_action.values(), key=lambda p: p["o0"])
    out = []
    for k, turn in enumerate(turns):
        nxt = turns[k + 1]["t"] if k + 1 < len(turns) else 1e9
        own = [i for i, a in enumerate(actions) if turn["t"] <= a["done"] < nxt and i in by_action]
        if not turn["text"]:
            continue
        if own:
            p = by_action[own[0]]
            later = [q for q in order if q["o0"] > p["o0"]]
            o1 = later[0]["o0"] - 0.15 if later else end
            out.append(
                {
                    "o0": round(max(0.0, p["o0"] - 1.4), 4),
                    "o1": round(o1, 4),
                    "text": turn["text"],
                    "action": own[0],
                }
            )
        else:
            out.append(
                {
                    "o0": round(turn["out"], 4),
                    "o1": round(end, 4),
                    "text": turn["text"],
                    "action": None,
                }
            )
    return out


# --- the check ---------------------------------------------------------------------------------


def masked_diff(frame: Image.Image, shot: Path) -> float:
    """Distance between a footage frame and a saved screenshot, ignoring the screenshot's
    painted-out (black) boxes, at half size."""
    ref = Image.open(shot).convert("L").resize((640, 400))
    live = frame.resize((640, 400))
    mask = ref.point([0 if v <= 12 else 255 for v in range(256)])
    diff = ImageChops.difference(live, ref)
    stat = ImageStat.Stat(diff, mask)
    return float(stat.mean[0])


def not_blank(frame: Image.Image, x: int, y: int) -> bool:
    """Something is under the point: the 24x24 pixels around it aren't one flat colour."""
    box = frame.crop((max(0, x - 12), max(0, y - 12), min(SRC[0], x + 12), min(SRC[1], y + 12)))
    return float(ImageStat.Stat(box).stddev[0]) > 6.0


@dataclass
class Result:
    scene: str
    name: str
    problems: list[str]


def check_action(
    f: Frames,
    a: dict[str, Any],
    *,
    shots: tuple[Path, Path] | None,
    bbox: dict[str, int] | None,
    expected: tuple[int, int] | None,
) -> list[str]:
    problems: list[str] = []
    if a.get("pre") is None:
        return ["no visible effect found near its logged time"]
    pre, onset = a["pre"], a["onset"]
    held = f.full(pre)
    before = f.full(max(0.0, pre - 0.1))
    after = f.full(onset + 0.35)
    box = (
        max(0, a["x"] - 80),
        max(0, a["y"] - 28),
        min(SRC[0], a["x"] + 80),
        min(SRC[1], a["y"] + 28),
    )
    own = (max(0, a["x"] - 20), max(0, a["y"] - 12), a["x"] + 20, a["y"] + 12)
    if mean_diff(held, before) > 2.0:
        problems.append("the held frame is mid-transition")
    effect = (
        mean_diff(held.crop(own), after.crop(own)) > 8.0
        or mean_diff(held.crop(box), after.crop(box)) > 3.0
        or mean_diff(held, after) > 1.0
    )
    if not effect:
        problems.append("nothing changes after the held frame, so it isn't the pre-action screen")
    if shots is not None and shots[0].is_file():
        d_before = masked_diff(held, shots[0])
        if shots[1].is_file():
            d_after = masked_diff(held, shots[1])
            if d_before > d_after + 0.25:
                problems.append(
                    "the held frame looks like the after screenshot "
                    f"({d_before:.2f} vs {d_after:.2f})"
                )
        elif d_before > 6.0:  # no after screenshot (the run stopped there): match the before
            problems.append(f"the held frame doesn't match the before screenshot ({d_before:.2f})")
    if bbox is not None:
        inside = (
            bbox["x"] - 2 <= a["x"] <= bbox["x"] + bbox["width"] + 2
            and bbox["y"] - 2 <= a["y"] <= bbox["y"] + bbox["height"] + 2
        )
        if not inside:
            problems.append(f"the ripple ({a['x']},{a['y']}) is outside the grounded target {bbox}")
    if expected is not None and abs(a["x"] - expected[0]) + abs(a["y"] - expected[1]) > 40:
        problems.append(
            f"the ripple ({a['x']},{a['y']}) is far from the recipe's recorded target {expected}"
        )
    if not not_blank(held, a["x"], a["y"]):
        problems.append(f"blank space under the ripple at ({a['x']},{a['y']})")
    return problems


# --- footage and run logs ---------------------------------------------------------------

# The console's holder field, as the corner label says it. While a run waits for help the lease
# is released and the holder is "nobody": the label says so rather than claim automation.
WHO = {"AWAITING_HUMAN": "No one", "HUMAN": "Person"}  # every other state: Automation


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


def events_of(run_dir: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (run_dir / "run.jsonl").open()]


def anchor_of(events: Sequence[dict[str, Any]]) -> float:
    """The harness's first navigation: the moment the sign-on page is painted, which is where a
    clip's trimmed start lines up with the run log."""
    return next(
        ts(e["ts"]) for e in events if e["kind"] == "surface.act" and e.get("action") == "navigate"
    )


def ts(text: str) -> float:
    return datetime.fromisoformat(text).timestamp()


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


def appears(
    f: Frames, region: Sequence[int], ref: float, lo: float = 0.0, threshold: float = 2.5
) -> float:
    """When a thing first appears and stays: the first source time from `lo` on after which the
    region (screen pixels: x0, y0, x1, y1) looks as it does at `ref`, where it is known to be on
    screen, in every frame up to `ref`."""
    sx, sy = SMALL[0] / SRC[0], SMALL[1] / SRC[1]
    x0, y0, x1, y1 = region
    box = (
        round(x0 * sx),
        round(y0 * sy),
        max(round(x1 * sx), round(x0 * sx) + 1),
        max(round(y1 * sy), round(y0 * sy) + 1),
    )
    target = f.small[f.index(ref)].crop(box)
    first = f.index(ref)
    for i in range(f.index(ref), f.index(lo) - 1, -1):
        if mean_diff(f.small[i].crop(box), target) > threshold:
            break
        first = i
    return first / FPS

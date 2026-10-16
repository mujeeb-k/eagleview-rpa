"""One frame of the rendered draft per click or keypress, at the moment its ripple plays,
labelled with the action: the visual half of the sync check in video/sync.py.

    PYTHONPATH=. uv run python video/sync_sheet.py

Reads video/public/media/story.json and the rendered film (evidence/demo.mp4, or the path given);
writes runs/demo_video/sync_sheet.png."""

import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[1]
STORY = REPO / "video" / "public" / "media" / "story.json"
FILM = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "evidence" / "demo.mp4"
OUT = REPO / "runs" / "demo_video" / "sync_sheet.png"
CELL = (640, 360)
BAND = 34
COLS = 4


def frame(t: float) -> Image.Image:
    w, h = CELL
    raw = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(FILM), "-frames:v", "1"]
        + ["-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True,
        check=True,
    ).stdout
    return Image.frombytes("RGB", CELL, raw)


def main() -> int:
    story = json.loads(STORY.read_text())
    entries = [e for e in story["sync"] if e["t"] is not None]
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 20)
    except OSError:
        font = ImageFont.load_default()
    rows = (len(entries) + COLS - 1) // COLS
    sheet = Image.new("RGB", (COLS * CELL[0], rows * (CELL[1] + BAND)), "#111")
    draw = ImageDraw.Draw(sheet)
    for n, e in enumerate(entries):
        x, y = (n % COLS) * CELL[0], (n // COLS) * (CELL[1] + BAND)
        sheet.paste(frame(e["t"]), (x, y + BAND))
        ok = "ok" if not e["problems"] else "BAD"
        draw.text(
            (x + 8, y + 6), f"{n + 1}. {e['scene']}: {e['name']}  [{ok}]", fill="#fff", font=font
        )
    sheet.save(OUT)
    print(f"{OUT.relative_to(REPO)}: {len(entries)} actions")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The evidence writer: the only path from a run to disk.

Layout of `runs/<run_id>/`:
    run.jsonl                     events `{ts, run_id, seq, actor, kind, ...}`, one per line
    steps/NNNN_before.png         screenshots with sensitive text boxes filled in
    steps/NNNN_after.png
    dom/<name>.redacted.html      DOM snapshots, scrubbed as whole strings
    summary.json, result.json     any JSON document

Every string goes through the redactor before it is serialized. Raw screenshots stay in memory:
the screenshot API requires the text boxes that say where to paint."""

import io
import json
import re
import threading
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw
from pydantic import BaseModel

from eagleview.safety.redaction import Redactor
from eagleview.schema.action import TextBox
from eagleview.schema.events import STRUCTURAL_FIELDS, Event

_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_FILL = (0, 0, 0)
_PAD = 2  # pixels around each painted box, so antialiased glyph edges are covered too


class EvidenceWriter:
    def __init__(self, runs_root: Path, run_id: str, redactor: Redactor) -> None:
        # Validate the id through the event schema before it becomes a directory name.
        Event(ts=datetime.now(UTC), run_id=run_id, seq=0, actor="system", kind="run.open")
        self.run_id = run_id
        self.redactor = redactor
        self.run_dir = runs_root / run_id
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self._seq = 0
        self._lock = threading.Lock()

    @property
    def events_path(self) -> Path:
        return self.run_dir / "run.jsonl"

    def event(self, kind: str, actor: str, **fields: object) -> dict[str, object]:
        """Append one redacted event and return it as written."""
        clash = STRUCTURAL_FIELDS & fields.keys()
        if clash:
            raise ValueError(f"event fields may not override {sorted(clash)}")
        with self._lock:
            header = Event(
                ts=datetime.now(UTC), run_id=self.run_id, seq=self._seq, actor=actor, kind=kind
            )
            record: dict[str, object] = {
                "ts": header.ts.isoformat(),
                "run_id": header.run_id,
                "seq": header.seq,
                "actor": header.actor,
                "kind": header.kind,
                **fields,
            }
            redacted = self.redactor.redact_tree(record, exempt_keys=STRUCTURAL_FIELDS)
            assert isinstance(redacted, dict)
            line = json.dumps(redacted, ensure_ascii=False, separators=(",", ":"))
            with self.events_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            self._seq += 1
            return redacted  # pyright: ignore[reportUnknownVariableType]

    def screenshot(
        self,
        step: int,
        phase: Literal["before", "after"],
        png: bytes,
        *,
        text_boxes: Sequence[TextBox],
    ) -> Path:
        """Paint over every text box the redactor flags, then write the PNG.

        `text_boxes` has no default on purpose: there is no way to write an unredacted screenshot.
        Pass an empty list only for an image known to carry no text."""
        if not 0 <= step <= 9999:
            raise ValueError("step must be 0..9999")
        path = self.run_dir / "steps" / f"{step:04d}_{phase}.png"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(self.redact_png(png, text_boxes))
        return path

    def redact_png(self, png: bytes, text_boxes: Sequence[TextBox]) -> bytes:
        with Image.open(io.BytesIO(png)) as source:
            image = source.convert("RGB")  # drops alpha and any metadata chunks
        draw = ImageDraw.Draw(image)
        for box in text_boxes:
            if not self.redactor.is_sensitive(box.text):
                continue
            x0 = max(0, box.x - _PAD)
            y0 = max(0, box.y - _PAD)
            x1 = min(image.width - 1, box.x + box.width + _PAD)
            y1 = min(image.height - 1, box.y + box.height + _PAD)
            if x1 >= x0 and y1 >= y0:
                draw.rectangle((x0, y0, x1, y1), fill=_FILL)
        out = io.BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()

    def dom_snapshot(self, name: str, html_text: str) -> Path:
        """Scrub the whole document: text nodes, attribute values (hidden inputs, `value=` on the
        member-number field) and inline script alike."""
        path = self.run_dir / "dom" / f"{self._checked(name)}.redacted.html"
        path.parent.mkdir(exist_ok=True)
        path.write_text(self.redactor.redact_text(html_text), encoding="utf-8")
        return path

    def write_json(self, name: str, data: BaseModel | Mapping[str, object]) -> Path:
        """`summary`, `result` and the like: written as `<name>.json`, redacted."""
        path = self.run_dir / f"{self._checked(name)}.json"
        redacted = self.redactor.redact_tree(data)
        path.write_text(json.dumps(redacted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def summary(self, data: BaseModel | Mapping[str, object]) -> Path:
        return self.write_json("summary", data)

    @staticmethod
    def _checked(name: str) -> str:
        if not _NAME.fullmatch(name) or ".." in name:
            raise ValueError(f"bad evidence file name {name!r}")
        return name

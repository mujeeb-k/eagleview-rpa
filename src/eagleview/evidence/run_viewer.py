"""`eagleview view <run_dir>`: a static `index.html` timeline for one run.

It reads only what the evidence writer already wrote (redacted `run.jsonl`, `summary.json`,
`trace.jsonl` and screenshots), so it can show nothing that is not already on disk, redacted.
Every value is HTML-escaped."""

import html
import json
from pathlib import Path
from typing import Any

_SHOWN = (
    "step.start",
    "surface.act",
    "state.detected",
    "recovery",
    "approval.granted",
    "approval.requested",
    "takeover.requested",
    "takeover.resumed",
    "network.blocked",
    "run.failure",
    "run.outcome",
    "discovery.turn",
    "discovery.handover",
    "patch.proposed",
)
_KEYS = (
    "step_id",
    "action",
    "status",
    "reason",
    "state",
    "handler",
    "category",
    "message",
    "code",
    "rationale",
    "intervention",
    "url",
    "path",
)


_REPORT_HEAD = "<tr><th>step</th><th>strategy</th><th>rank</th><th>agreement</th></tr>"
_TIMELINE_HEAD = "<tr><th>#</th><th>actor</th><th>event</th><th>detail</th></tr>"


def _lines(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def render(run_dir: Path, *, label: str | None = None) -> Path:
    summary: dict[str, Any] = {}
    if (run_dir / "summary.json").is_file():
        summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    events = [e for e in _lines(run_dir / "run.jsonl") if e.get("kind") in _SHOWN]
    shots = (
        sorted(p.name for p in (run_dir / "steps").glob("*.png"))
        if (run_dir / "steps").is_dir()
        else []
    )
    e = html.escape
    head = summary.get("capability") or summary.get("goal") or run_dir.name
    status = summary.get("status", "?")
    rows = []
    for ev in events:
        detail = "; ".join(f"{k}={ev[k]}" for k in _KEYS if ev.get(k) not in (None, "", []))
        rows.append(
            f"<tr><td>{e(str(ev.get('seq')))}</td><td>{e(str(ev.get('actor')))}</td>"
            f"<td>{e(str(ev.get('kind')))}</td><td>{e(detail)}</td></tr>"
        )
    report = "".join(
        f"<tr><td>{e(r['step_id'])}</td><td>{e(r['strategy'])}</td><td>{r['rank']}</td>"
        f"<td>{e(str(r.get('agreement')))}</td></tr>"
        for r in summary.get("locator_report") or []
    )
    recov = "".join(
        f"<li>{e(r['handler'])} at {e(str(r.get('at_step')))}</li>"
        for r in summary.get("recoveries") or []
    )
    failure = summary.get("failure") or {}
    gallery = "".join(
        f'<figure><img src="steps/{e(n)}" loading="lazy"><figcaption>{e(n)}</figcaption></figure>'
        for n in shots
    )
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>{e(str(head))}</title>
<style>body{{font:14px system-ui;margin:24px;max-width:1300px}}td,th{{padding:3px 8px;
border-bottom:1px solid #ddd;text-align:left;vertical-align:top}}img{{width:420px;border:1px solid
#999}}figure{{display:inline-block;margin:6px}}.label{{background:#fee;padding:6px}}</style>
</head><body><h1>{e(str(head))}</h1>
{f'<p class="label">{e(label)}</p>' if label else ""}
<p>Run {e(run_dir.name)}: <b>{e(str(status))}</b>
{e(str(failure.get("category", "")))} {e(str(failure.get("message", "")))}</p>
<h2>Recoveries</h2><ul>{recov or "<li>none</li>"}</ul>
<h2>Locator report</h2><table>{_REPORT_HEAD}{report}</table>
<h2>Timeline</h2><table>{_TIMELINE_HEAD}{"".join(rows)}</table>
<h2>Screenshots (redacted)</h2>{gallery or "<p>none</p>"}
</body></html>"""
    out = run_dir / "index.html"
    out.write_text(page, encoding="utf-8")
    return out

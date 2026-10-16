"""Records the demo video, the one exception to invariant 4 (docs/DECISIONS.md, "Demo video
exception"). Only with --record-video, and only against the synthetic mock bank: Playwright
writes webm into a temporary folder, ffmpeg transcodes it to evidence/demo.mp4 (under 3
minutes), and the webm files are deleted. The PII scan cannot read video; the synthetic-only
rule covers it."""

import argparse
import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from eagleview.artifacts.loader import load_for_run, tenant_overlays
from eagleview.artifacts.yaml_io import load_app_profile, load_policy, load_tenant
from eagleview.runner import environment, run_replay
from eagleview.surfaces.web.launch import launched_browser
from mock_bank.harness import running_banks

REPO = Path(__file__).resolve().parents[2]


async def record(video_dir: Path) -> None:
    lookup = load_for_run(REPO / "capabilities/drafts/member.lookup_share_balances.yaml").capability
    profile = load_app_profile(REPO / "app_profiles/ironbridge-teller.yaml")
    with running_banks(REPO / ".env") as banks:
        policy = load_policy(REPO / "policies/training-teller.yaml").model_copy(
            update={"allowed_origins": {s: [b.url] for s, b in banks.items()}}
        )
        env = {**environment(), **{b.tenant.password_env: b.password for b in banks.values()}}
        async with launched_browser() as browser:
            for slug, member, faults in (
                ("lantern-bay", "10482", {}),
                ("lantern-bay", "99999", {}),
                ("lantern-bay", "10482", {"system_notice_on": "/member"}),
                ("copperhill", "2210236", {}),
            ):
                bank = banks[slug]
                bank.reset()
                if faults:
                    bank.set_faults(**faults)
                tenant = load_tenant(REPO / f"tenants/{slug}.yaml").model_copy(
                    update={"base_url": bank.url}
                )
                await run_replay(
                    lookup,
                    {"member_id": member},
                    profile=profile,
                    tenant=tenant,
                    policy=policy,
                    browser=browser,
                    runs_root=Path(tempfile.mkdtemp()),
                    environ=env,
                    overlays=tenant_overlays(slug, REPO / "overlays"),
                    record_video_dir=video_dir,  # explicit: this script, with --record-video, only
                )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-video", action="store_true", help="required: actually record")
    args = parser.parse_args()
    if not args.record_video:
        print("Refusing to record without --record-video.", file=sys.stderr)
        return 2
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        print("ffmpeg is needed to transcode the recording (brew install ffmpeg).", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        video_dir = Path(tmp)
        asyncio.run(record(video_dir))
        clips = sorted(video_dir.glob("*.webm"), key=lambda p: p.stat().st_mtime)
        listing = video_dir / "clips.txt"
        listing.write_text("".join(f"file '{c}'\n" for c in clips))
        out = REPO / "evidence" / "demo.mp4"
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listing),
                "-t",
                "175",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(out),
            ],
            check=True,
        )
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

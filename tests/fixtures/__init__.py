"""Hand-written fixtures for tests only. Never copied into capabilities/ or overlays/."""

from pathlib import Path

FIXTURES = Path(__file__).parent
CAPABILITY = FIXTURES / "capabilities" / "member.lookup_share_balances.yaml"
OVERLAY = FIXTURES / "overlays" / "copperhill" / "ironbridge-teller.yaml"
REPO = FIXTURES.parent.parent
# The app profile is authored, not discovered, so tests use the committed one.
APP_PROFILE = REPO / "app_profiles" / "ironbridge-teller.yaml"
POLICY = REPO / "policies" / "default-teller.yaml"
TRAINING_POLICY = REPO / "policies" / "training-teller.yaml"
OPEN_SHARE = FIXTURES / "capabilities" / "member.open_share.yaml"
STALE_EXPECT = FIXTURES / "capabilities" / "test.stale_expect.yaml"

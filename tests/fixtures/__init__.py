"""Hand-written fixtures for tests only. Never copied into capabilities/ or overlays/."""

from pathlib import Path

FIXTURES = Path(__file__).parent
CAPABILITY = FIXTURES / "capabilities" / "member.lookup_share_balances.yaml"
APP_PROFILE = FIXTURES / "app_profiles" / "ironbridge-teller.yaml"
OVERLAY = FIXTURES / "overlays" / "copperhill" / "ironbridge-teller.yaml"
REPO = FIXTURES.parent.parent
POLICY = REPO / "policies" / "default-teller.yaml"

"""Cross-tenant reuse: the one discovered capability runs on both tenants. Copperhill needs only
its overlays (aliases, the Select Branch interstitial, one step patch), and data never crosses
tenants."""

from decimal import Decimal
from functools import cache
from typing import Any

import pytest

from eagleview.artifacts.loader import load_for_run, tenant_overlays
from eagleview.artifacts.yaml_io import load_app_profile
from eagleview.overlays.resolve import OverlayError, resolve
from eagleview.schema.hashing import content_hash
from eagleview.schema.overlay import Overlay
from mock_bank.config import all_tenants
from mock_bank.seed import Member, generate
from tests.fixtures import APP_PROFILE, OPEN_SHARE, REPO
from tests.support.bank import Bank
from tests.support.replay import Replay

LOOKUP = REPO / "capabilities" / "drafts" / "member.lookup_share_balances.yaml"
PROFILE = load_app_profile(APP_PROFILE)


def copperhill_overlays() -> list[Overlay]:
    overlays = tenant_overlays("copperhill", REPO / "overlays")
    assert len(overlays) == 2
    return overlays


@cache
def members(slug: str) -> dict[str, Member]:
    return generate(next(t for t in all_tenants() if t.slug == slug))


def shares(slug: str, number: str) -> list[dict[str, Any]]:
    return [{"desc": s.description, "balance": s.balance} for s in members(slug)[number].shares]


# --- resolution -----------------------------------------------------------------------------------


def test_the_copperhill_overlays_patch_the_lookup() -> None:
    cap = load_for_run(LOOKUP).capability
    tenant = tenant_for_slug("copperhill")
    eff = resolve(cap, PROFILE, tenant, copperhill_overlays())
    search = next(s for s in eff.capability.steps if s.id == "submit_member_search")
    assert search.target is not None
    assert search.target.locators[0].model_dump() == {
        "strategy": "a11y",
        "role": "button",
        "name": "Search",
    }
    assert [s.id for s in eff.profile.global_states][-1] == "select_branch_interstitial"
    assert eff.label_aliases["Member #"] == ["Acct Holder No."]
    assert eff.effective_hash != content_hash(cap)
    assert eff.capability.status == "verified"
    # The base capability is untouched.
    base = next(s for s in cap.steps if s.id == "submit_member_search").target
    assert base is not None and base.locators[0].strategy == "anchor"


def test_no_overlay_means_the_effective_hash_is_the_content_hash() -> None:
    cap = load_for_run(LOOKUP).capability
    eff = resolve(cap, PROFILE, tenant_for_slug("lantern-bay"), copperhill_overlays())
    assert eff.effective_hash == content_hash(cap) and eff.applied == []


def test_a_profile_wide_overlay_fits_every_capability() -> None:
    cap = load_for_run(OPEN_SHARE).capability  # has no submit_member_search step
    eff = resolve(cap, PROFILE, tenant_for_slug("copperhill"), copperhill_overlays())
    assert len(eff.applied) == 1  # only the profile-wide one applies to this capability


def test_an_overlay_naming_a_missing_step_of_its_own_capability_is_an_error() -> None:
    cap = load_for_run(LOOKUP).capability
    bad = Overlay.model_validate(
        {
            "applies_to": {
                "profile": "ironbridge-teller",
                "capabilities": ["member.lookup_share_balances"],
            },
            "tenant": "copperhill",
            "step_patches": {"no_such_step": {"timeout_ms": 9000}},
        }
    )
    with pytest.raises(OverlayError, match="no step 'no_such_step'"):
        resolve(cap, PROFILE, tenant_for_slug("copperhill"), [bad])


def test_version_patches_follow_the_declared_version() -> None:
    cap = load_for_run(LOOKUP).capability
    patch = Overlay.model_validate(
        {
            "applies_to": {"profile": "ironbridge-teller", "versions": ">=7.4,<7.5"},
            "step_patches": {"submit_member_search": {"timeout_ms": 12000}},
        }
    )
    on_74 = resolve(cap, PROFILE, tenant_for_slug("copperhill"), [patch])
    on_72 = resolve(cap, PROFILE, tenant_for_slug("lantern-bay"), [patch])
    assert (
        next(s for s in on_74.capability.steps if s.id == "submit_member_search").timeout_ms
        == 12000
    )
    assert on_72.applied == []


def tenant_for_slug(slug: str) -> Any:
    from eagleview.artifacts.yaml_io import load_tenant

    return load_tenant(REPO / "tenants" / f"{slug}.yaml")


# --- the proof, against both live tenants ---------------------------------------------------------


async def test_the_same_capability_runs_on_both_tenants_each_with_its_own_member(
    replay: Replay, lantern: Bank, copperhill: Bank
) -> None:
    at_lantern = await replay(lantern, {"member_id": lantern.demo}, capability=LOOKUP)
    assert at_lantern.result.status == "success", at_lantern.result.failure
    at_copperhill = await replay(
        copperhill,
        {"member_id": copperhill.demo},
        capability=LOOKUP,
        overlays=copperhill_overlays(),
    )
    r = at_copperhill.result
    assert r.status == "success", r.failure
    rows = r.outputs["share_balances"]  # type: ignore[index]
    assert [(x["share_description"], x["balance"]) for x in rows] == [  # type: ignore[index, union-attr]
        (s["desc"], s["balance"]) for s in shares("copperhill", copperhill.demo)
    ]
    assert all(isinstance(x["balance"], Decimal) for x in rows)  # type: ignore[index, union-attr]
    # How it succeeded: the menu link by its accessible name through the alias (the anchor below
    # FUNCTIONS would find Cash Drawer here), and the search by the patched Search button.
    report = {x.step_id: (x.strategy, x.rank) for x in r.locator_report}
    assert report["open_member_inquiry"] == ("a11y", 0)
    assert report["submit_member_search"] == ("a11y", 0)
    assert [h.handler for h in r.recoveries] == ["select_branch_interstitial.select_then_click"]
    assert r.effective_hash != at_lantern.result.effective_hash


async def test_a_lantern_bay_member_at_copperhill_is_not_found(
    replay: Replay, copperhill: Bank
) -> None:
    run = await replay(
        copperhill, {"member_id": "10482"}, capability=LOOKUP, overlays=copperhill_overlays()
    )
    assert run.result.status == "outcome"
    assert run.result.outcome is not None and run.result.outcome.code == "MEMBER_NOT_FOUND"


async def test_copperhill_session_expiry_passes_select_branch_again(
    replay: Replay, copperhill: Bank
) -> None:
    # Copperhill's sign-on makes 6 authenticated requests: GET and POST /branch, then the frameset
    # (frames, banner, nav, home). So the 7th, the first step's GET /inq, finds the session gone.
    copperhill.set_faults(session_expire_after_requests="6")
    run = await replay(
        copperhill,
        {"member_id": copperhill.demo},
        capability=LOOKUP,
        overlays=copperhill_overlays(),
    )
    r = run.result
    assert r.status == "success", r.failure
    handlers = [h.handler for h in r.recoveries]
    assert handlers.count("select_branch_interstitial.select_then_click") == 2
    assert "session_expired.relogin_and_restart" in handlers


async def test_nothing_from_a_copperhill_run_reaches_disk_readable(
    replay: Replay, copperhill: Bank
) -> None:
    run = await replay(
        copperhill,
        {"member_id": copperhill.demo},
        capability=LOOKUP,
        overlays=copperhill_overlays(),
    )
    assert run.result.status == "success"
    written = run.files_text()
    m = members("copperhill")[copperhill.demo]
    assert copperhill.demo not in written and m.display_name not in written

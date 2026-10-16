"""Every locator in the committed app profile, resolved against the live mock bank: each one
alone must find exactly one element, on both tenants, because replay takes the first unique."""

from typing import Any

import pytest

from eagleview.artifacts.yaml_io import load_app_profile
from eagleview.schema.app_profile import Dismiss
from eagleview.schema.locators import TargetSpec
from eagleview.surfaces.base import Unique
from tests.fixtures import APP_PROFILE
from tests.support.bank import Bank
from tests.support.teller import sign_on

PROFILE = load_app_profile(APP_PROFILE)


def one_by_one(spec: TargetSpec) -> list[TargetSpec]:
    return [spec.model_copy(update={"locators": [loc]}) for loc in spec.locators]


async def test_every_login_locator_is_unique_on_both_tenants(open_surface: Any, bank: Bank) -> None:
    surface = await open_surface(bank, actor="harness", signed_on=False)
    await surface.page.goto(bank.url + PROFILE.login.entry_path)
    for macro in PROFILE.login.steps:
        for single in one_by_one(macro.target):
            found = await surface.resolve(single)
            strategy = single.locators[0].strategy
            assert isinstance(found, Unique), f"{macro.id} by {strategy}: {found}"
            assert not found.occluded


async def test_the_version_footer_is_on_screen_at_sign_on(open_surface: Any, bank: Bank) -> None:
    surface = await open_surface(bank, actor="harness", signed_on=False)
    await surface.page.goto(bank.url + PROFILE.login.entry_path)
    assert await surface.check(PROFILE.fingerprint.detect)
    texts = " ".join(b.text for b in await surface.text_boxes())
    assert f"v{bank.tenant.version}" in texts


@pytest.mark.parametrize(
    "state", [s for s in PROFILE.global_states if isinstance(s.recover, Dismiss)]
)
async def test_every_dismiss_locator_is_unique(
    open_surface: Any, lantern: Bank, state: Any
) -> None:
    lantern.set_faults(system_notice_on="/home")
    surface = await open_surface(lantern, signed_on=False)
    await sign_on(surface.page, lantern)
    assert await surface.check(state.detect)
    for single in one_by_one(state.recover.click):
        found = await surface.resolve(single)
        assert isinstance(found, Unique) and not found.occluded, single.locators[0].strategy

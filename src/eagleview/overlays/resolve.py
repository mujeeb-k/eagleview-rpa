"""Overlay resolution: base capability, then app-profile version patches, then tenant overlays.
The result is the effective artifact, and its hash is recorded on every run.

An overlay applies when its profile matches, its tenant matches (or it names none), the
capability id matches one of its patterns, and, for a version patch, the tenant's declared
`app_version` is in its range.

- **Label aliases** merge. The resolver's anchor, text and a11y strategies read them, and so does
  the evidence writer's sensitive-label rule.
- **Global states** are added after the app profile's own, so they are checked in every wait.
- **Step patches** are keyed by semantic step id. They can prepend, append or replace locators,
  and replace an expect or a timeout. A patch naming a missing step is an error when the overlay
  names this capability explicitly; under a glob it is skipped, so a profile-wide overlay fits
  every capability."""

import fnmatch
import json
from dataclasses import dataclass, field
from typing import Any

from eagleview.schema.app_profile import AppProfile
from eagleview.schema.capability import Capability
from eagleview.schema.common import version_in_range
from eagleview.schema.hashing import canonical_json, content_hash, sha256_of
from eagleview.schema.overlay import Overlay, StepPatch
from eagleview.schema.tenant import TenantProfile


class OverlayError(ValueError):
    pass


@dataclass(frozen=True)
class Effective:
    capability: Capability
    profile: AppProfile
    label_aliases: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    applied: list[str] = field(default_factory=list[str])  # "<tenant or version>:<patterns>"
    effective_hash: str = ""


def _applies(o: Overlay, cap: Capability, profile: AppProfile, tenant: TenantProfile) -> bool:
    a = o.applies_to
    if a.profile != profile.id or (o.tenant is not None and o.tenant != tenant.slug):
        return False
    if not any(fnmatch.fnmatchcase(cap.id, p) for p in a.capabilities):
        return False
    if a.versions is not None:
        if tenant.app_version is None:
            return False
        return version_in_range(tenant.app_version, a.versions)
    return True


def _patch_step(step: dict[str, Any], patch: StepPatch) -> None:
    if patch.target is not None:
        target = step.get("target")
        if target is None:
            raise OverlayError(f"step {step['id']!r} has no target to patch")
        t = patch.target
        if t.description is not None:
            target["description"] = t.description
        if t.frame is not None:
            target["frame"] = list(t.frame)
        if t.locators is not None:
            dump = [loc.model_dump(mode="json") for loc in t.locators.prepend]
            tail = [loc.model_dump(mode="json") for loc in t.locators.append]
            if t.locators.replace is not None:
                middle = [loc.model_dump(mode="json") for loc in t.locators.replace]
            else:
                middle = target["locators"]
            target["locators"] = [*dump, *middle, *tail]
            target.pop("fragile", None)  # derived again from the new locator list
    if patch.expect is not None:
        step["expect"] = patch.expect.model_dump(mode="json", by_alias=True)
    if patch.timeout_ms is not None:
        step["timeout_ms"] = patch.timeout_ms


def resolve(
    capability: Capability,
    profile: AppProfile,
    tenant: TenantProfile,
    overlays: list[Overlay],
) -> Effective:
    # Version patches (no tenant) first, then tenant overlays; profile-wide before specific.
    ordered = sorted(
        (o for o in overlays if _applies(o, capability, profile, tenant)),
        key=lambda o: (o.tenant is not None, o.applies_to.capabilities != ["*"]),
    )
    if not ordered:
        return Effective(capability, profile, {}, [], content_hash(capability))
    data = capability.model_dump(mode="json", by_alias=True, exclude_none=True)
    steps = {s["id"]: s for s in data["steps"]}
    states = list(profile.global_states)
    aliases: dict[str, list[str]] = {}
    applied: list[str] = []
    for o in ordered:
        for label, extra in o.label_aliases.items():
            merged = aliases.setdefault(label, [])
            merged += [x for x in extra if x not in merged]
        taken = {s.id for s in states}
        for state in o.global_states_add:
            if state.id in taken:
                raise OverlayError(f"global state {state.id!r} is already defined")
            states.append(state)
        explicit = capability.id in o.applies_to.capabilities
        for sid, patch in o.step_patches.items():
            if sid not in steps:
                if explicit:
                    raise OverlayError(f"{capability.ref} has no step {sid!r} to patch")
                continue
            _patch_step(steps[sid], patch)
        applied.append(f"{o.tenant or o.applies_to.versions}:{','.join(o.applies_to.capabilities)}")
    effective = Capability.model_validate(data)
    effective_profile = profile.model_copy(update={"global_states": states})
    digest = sha256_of(
        json.dumps(
            {
                "capability": canonical_json(effective, exclude={"provenance"}),
                "overlays": [canonical_json(o) for o in ordered],
            },
            sort_keys=True,
        )
    )
    return Effective(effective, effective_profile, aliases, applied, digest)

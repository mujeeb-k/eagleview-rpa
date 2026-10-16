"""Finding and loading what a run needs: the capability, its app profile, the tenant and the
policy. Paths default to the committed directories at the repository root."""

from dataclasses import dataclass
from pathlib import Path

from eagleview.artifacts.yaml_io import (
    ArtifactHashMismatch,
    dump_model,
    load_capability,
    load_model,
    load_overlay,
)
from eagleview.schema.capability import Capability
from eagleview.schema.hashing import with_content_hash
from eagleview.schema.overlay import Overlay

CAPABILITIES = Path("capabilities")
DRAFTS = CAPABILITIES / "drafts"
APP_PROFILES = Path("app_profiles")
TENANTS = Path("tenants")
OVERLAYS = Path("overlays")
DEFAULT_POLICY = Path("policies/default-teller.yaml")


@dataclass(frozen=True)
class LoadedCapability:
    capability: Capability
    path: Path
    # Verified or approved, edited since, and loaded as a draft with --allow-modified.
    modified: bool = False


def capability_path(ref: str, root: Path = CAPABILITIES) -> Path:
    """A path to a YAML file, or a capability id looked up as `<root>/<id>.yaml`."""
    as_path = Path(ref)
    if as_path.suffix in (".yaml", ".yml"):
        if not as_path.is_file():
            raise FileNotFoundError(f"no capability file at {as_path}")
        return as_path
    found = root / f"{ref}.yaml"
    if not found.is_file():
        raise FileNotFoundError(f"no capability {ref!r} in {root}/ (looked for {found})")
    return found


def load_for_run(path: Path, *, allow_modified: bool = False) -> LoadedCapability:
    """Load a capability for replay. An edited verified or approved artifact is refused, with a
    message naming the file and the two ways to experiment; `allow_modified` runs it as a draft."""
    try:
        return LoadedCapability(load_capability(path), path)
    except ArtifactHashMismatch:
        if not allow_modified:
            raise
    edited = load_model(path.read_text(encoding="utf-8"), Capability)
    as_draft = with_content_hash(edited.model_copy(update={"status": "draft"}))
    return LoadedCapability(as_draft, path, modified=True)


def app_profile_path(profile: str, root: Path = APP_PROFILES) -> Path:
    return root / f"{profile}.yaml"


def tenant_path(slug: str, root: Path = TENANTS) -> Path:
    return root / f"{slug}.yaml"


def write_draft(capability: Capability, root: Path = DRAFTS) -> Path:
    """Write a compiled capability under `capabilities/drafts/`, never over an existing file: a
    name that is taken gets a numbered suffix (`<id>.2.yaml`, ...). The file is created
    exclusively, so two writers cannot clobber each other either. Promoting a draft into
    `capabilities/` is a reviewed step, never done here."""
    root.mkdir(parents=True, exist_ok=True)
    text = dump_model(with_content_hash(capability))
    n = 1
    while True:
        path = root / (f"{capability.id}.yaml" if n == 1 else f"{capability.id}.{n}.yaml")
        try:
            with path.open("x", encoding="utf-8") as fh:
                fh.write(text)
            return path
        except FileExistsError:
            n += 1


def tenant_overlays(slug: str, root: Path = OVERLAYS) -> list[Overlay]:
    """Every overlay a tenant has, from `overlays/<slug>/`. Which ones apply is the resolver's
    decision (profile, capability, version)."""
    folder = root / slug
    return [load_overlay(p) for p in sorted(folder.glob("*.yaml"))] if folder.is_dir() else []

"""Finding and loading what a run needs: the capability, its app profile, the tenant and the
policy. Paths default to the committed directories at the repository root."""

from dataclasses import dataclass
from pathlib import Path

from eagleview.artifacts.yaml_io import (
    ArtifactHashMismatch,
    load_capability,
    load_model,
)
from eagleview.schema.capability import Capability
from eagleview.schema.hashing import with_content_hash

CAPABILITIES = Path("capabilities")
APP_PROFILES = Path("app_profiles")
TENANTS = Path("tenants")
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

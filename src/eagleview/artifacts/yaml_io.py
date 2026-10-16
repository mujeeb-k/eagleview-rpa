"""YAML load and dump for every artifact type.

Key order is preserved both ways: a file loads in its own order, and a model dumps in field
declaration order, with mapping fields (inputs, outputs, columns) in insertion order. Dumping goes
through the model, so YAML comments do not survive a round trip; dump -> load -> dump is
byte-identical."""

import io
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import LiteralScalarString

from eagleview.schema.app_profile import AppProfile
from eagleview.schema.capability import Capability
from eagleview.schema.common import UnsupportedSchemaVersion, check_schema_version
from eagleview.schema.hashing import content_hash, with_content_hash
from eagleview.schema.overlay import Overlay
from eagleview.schema.policy import Policy
from eagleview.schema.tenant import TenantProfile


class ArtifactHashMismatch(ValueError):
    """A verified or approved artifact was edited after its hash was recorded."""


def _loader() -> YAML:
    return YAML(typ="safe", pure=True)


def _dumper() -> YAML:
    yaml = YAML(typ="rt")
    yaml.default_flow_style = False
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 100
    yaml.allow_unicode = True
    return yaml


def _commented(value: Any) -> Any:
    # Plain dicts would go through the safe representer; CommentedMap keeps insertion order.
    if isinstance(value, dict):
        out = CommentedMap()
        for key, item in value.items():  # pyright: ignore[reportUnknownVariableType]
            out[key] = _commented(item)
        return out
    if isinstance(value, list):
        return CommentedSeq([_commented(item) for item in value])  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, str) and "\n" in value.rstrip("\n"):
        # Multi-line prose reads best, and diffs best, as a literal block.
        return LiteralScalarString(value)
    return value


def load_yaml(text: str) -> Any:
    return _loader().load(text)


def dump_model(model: BaseModel) -> str:
    """YAML for a model. Decimals and other non-YAML scalars are dumped as quoted strings."""
    data = model.model_dump(mode="json", by_alias=True, exclude_none=True)
    stream = io.StringIO()
    _dumper().dump(_commented(data), stream)
    return stream.getvalue()


def load_model[M: BaseModel](text: str, model: type[M]) -> M:
    """Validate YAML text as `model`. An unsupported schema_version major is refused up front,
    with UnsupportedSchemaVersion, before any field-level error could obscure it."""
    data = load_yaml(text)
    if isinstance(data, dict) and "schema_version" in data:
        check_schema_version(data["schema_version"])
    return model.model_validate(data)


def _load_file[M: BaseModel](path: Path, model: type[M]) -> M:
    try:
        return load_model(path.read_text(encoding="utf-8"), model)
    except UnsupportedSchemaVersion as exc:
        raise UnsupportedSchemaVersion(f"{path}: {exc}") from exc


def load_capability(path: Path) -> Capability:
    """Load a capability and check its recorded hash.

    A verified or approved artifact whose content no longer matches its hash was edited after
    review and is refused. A draft's hash is simply recomputed."""
    capability = _load_file(path, Capability)
    recorded = capability.provenance.content_hash
    actual = content_hash(capability)
    if capability.status in ("verified", "approved"):
        if recorded is None:
            raise ArtifactHashMismatch(
                f"{path}: a {capability.status} artifact must carry content_hash"
            )
        if recorded != actual:
            raise ArtifactHashMismatch(
                f"{path}: content changed after it was {capability.status} "
                f"(recorded {recorded}, actual {actual}). To experiment with an edited copy, "
                "set `status: draft` in the file, or replay it with --allow-modified, which runs "
                "it as a draft, records `modified: true`, and is refused for unattended runs."
            )
        return capability
    return with_content_hash(capability)


def dump_capability(capability: Capability, path: Path) -> Capability:
    """Write a capability with its current hash and return what was written."""
    hashed = with_content_hash(capability)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_model(hashed), encoding="utf-8")
    return hashed


def load_app_profile(path: Path) -> AppProfile:
    return _load_file(path, AppProfile)


def load_overlay(path: Path) -> Overlay:
    return _load_file(path, Overlay)


def load_policy(path: Path) -> Policy:
    return _load_file(path, Policy)


def load_tenant(path: Path) -> TenantProfile:
    return _load_file(path, TenantProfile)

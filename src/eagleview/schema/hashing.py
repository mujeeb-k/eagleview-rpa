"""Canonical content hashes. A capability's hash covers everything except its provenance, so it
names exactly what replays, and changes whenever behaviour could change."""

import hashlib
import json

from pydantic import BaseModel

from eagleview.schema.capability import Capability


def canonical_json(model: BaseModel, *, exclude: set[str] | None = None) -> str:
    """Sorted keys, no whitespace, UTF-8, None-valued fields omitted.

    Omitting None (rather than every default) keeps old hashes stable when a new optional field
    defaults to None, while a changed non-None default still changes the hash, because it changes
    behaviour. New optional fields must therefore default to None."""
    data = model.model_dump(mode="json", by_alias=True, exclude_none=True, exclude=exclude)
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_of(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(capability: Capability) -> str:
    return sha256_of(canonical_json(capability, exclude={"provenance"}))


def with_content_hash(capability: Capability) -> Capability:
    """A copy whose provenance carries its current content hash."""
    provenance = capability.provenance.model_copy(update={"content_hash": content_hash(capability)})
    return capability.model_copy(update={"provenance": provenance})

"""JSON Schema export. Pydantic is the source of truth; `schemas/` is generated from it."""

import json
from pathlib import Path

from pydantic import BaseModel

from eagleview.schema.app_profile import AppProfile
from eagleview.schema.capability import Capability
from eagleview.schema.overlay import Overlay
from eagleview.schema.policy import Policy
from eagleview.schema.result import RunResult
from eagleview.schema.tenant import TenantProfile

EXPORTS: dict[str, type[BaseModel]] = {
    "capability": Capability,
    "app_profile": AppProfile,
    "overlay": Overlay,
    "policy": Policy,
    "run_result": RunResult,
    "tenant": TenantProfile,
}


def render_schemas() -> dict[str, str]:
    """File name -> JSON text, deterministic."""
    out: dict[str, str] = {}
    for name, model in EXPORTS.items():
        schema = model.model_json_schema(by_alias=True, mode="validation")
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"https://eagleview.local/schemas/{name}.schema.json"
        out[f"{name}.schema.json"] = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    return out


def export_schemas(directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, text in render_schemas().items():
        path = directory / filename
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written

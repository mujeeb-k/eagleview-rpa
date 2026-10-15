"""Tenant configuration: one YAML file per credit union running Ironbridge Teller."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict
from ruamel.yaml import YAML

TENANTS_DIR = Path(__file__).parent / "tenants"

# The training password is a secret like any other: it is read from this environment variable,
# never committed. The harness resolves `secret://ironbridge_teller_password` to the same variable.
PASSWORD_ENV = "IRONBRIDGE_TELLER_PASSWORD"

NavKey = Literal["inquiry", "totals", "drawer", "reports", "help", "signoff"]


class Theme(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    banner_bg: str
    banner_fg: str
    nav_bg: str
    nav_link: str
    page_bg: str
    bar_bg: str
    bar_fg: str
    font_face: str


class Labels(BaseModel):
    """Screen labels that differ between tenants. Everything else is fixed by the product."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    member_inquiry: str
    member_no: str


class Tenant(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: str
    name: str
    short_name: str
    version: str
    port: int
    seed: int
    username: str
    teller_id: str
    drawer: str
    branches: list[str]
    select_branch: bool
    search_button: Literal["image", "text"]
    nav: list[NavKey]
    labels: Labels
    theme: Theme
    injection_member: str
    txn_base: int

    @property
    def cookie_name(self) -> str:
        # Cookies are scoped by host, not port, so both tenants on 127.0.0.1 need distinct names.
        return "IBTSESS_" + self.slug.upper().replace("-", "_")

    @property
    def footer(self) -> str:
        return f"Ironbridge Teller v{self.version} — TRAINING REGION — synthetic data"


def load_tenant(slug: str) -> Tenant:
    data = YAML(typ="safe").load((TENANTS_DIR / f"{slug}.yaml").read_text())
    return Tenant.model_validate(data)


def all_tenants() -> list[Tenant]:
    return [load_tenant(p.stem) for p in sorted(TENANTS_DIR.glob("*.yaml"))]

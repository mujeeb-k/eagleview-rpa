"""Tenant configuration: one YAML file per credit union running Ironbridge Teller."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict
from ruamel.yaml import YAML

TENANTS_DIR = Path(__file__).parent / "tenants"

NavKey = Literal["inquiry", "totals", "drawer", "reports", "help", "signoff"]

# The role a share type plays in the product. Each tenant names and numbers its own types.
ShareKind = Literal["base", "draft", "club", "money_market"]


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


class ShareType(BaseModel):
    """One tenant's share product: its label and the suffix numbers it is allocated from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    description: str
    low: int
    high: int
    product: str | None = None  # code in the Open Share select; None: cannot be opened there


class Tenant(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: str
    name: str
    short_name: str
    version: str
    port: int
    seed: int
    first_member: int  # members are numbered upward from here; the digit count is per tenant
    demo_member: str
    username: str
    # The training password is a secret like any other: never committed. It is read from the
    # environment variable named by uppercasing this secret name (secret://<name> -> <NAME>).
    password_secret: str
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
    suffix_prefix: str
    share_types: dict[ShareKind, ShareType]

    @property
    def password_env(self) -> str:
        return self.password_secret.upper()

    def suffix(self, n: int) -> str:
        return f"{self.suffix_prefix}{n:02d}"

    @property
    def products(self) -> dict[str, ShareKind]:
        """Open Share select codes, mapped to the share kind each one opens."""
        return {st.product: kind for kind, st in self.share_types.items() if st.product}

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

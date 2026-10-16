"""Tenant configuration: one YAML file per credit union running Ironbridge Teller."""

from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator
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


class EdgeCases(BaseModel):
    """Members pinned to awkward but realistic shapes, so parsers and replay meet them on purpose.
    Applied after the seeded draws, so every other member is unchanged."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    overdrawn_member: str  # share draft below zero, shown in legacy trailing-minus form
    overdrawn_balance: str  # a negative decimal string, e.g. "-12.34"
    loans_only_member: str  # loans, but no share accounts at all
    hyphenated_member: str
    hyphenated_last: str  # an apostrophe and a hyphen, e.g. O'Brien-Smith


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
    edge_cases: EdgeCases

    @model_validator(mode="after")
    def _edge_members_are_ordinary_members(self) -> Self:
        e = self.edge_cases
        special = [e.overdrawn_member, e.loans_only_member, e.hyphenated_member]
        if len(set(special)) != 3 or {self.demo_member, self.injection_member} & set(special):
            raise ValueError("edge-case members must be distinct from each other and the demo")
        if not e.overdrawn_balance.startswith("-"):
            raise ValueError("overdrawn_balance must be negative")
        return self

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

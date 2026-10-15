"""A tenant: one institution's deployment of an app profile. Data and credentials never cross
tenants; only the capability contract is shared."""

from pydantic import BaseModel, ConfigDict, Field

from eagleview.schema.common import Name, SchemaVersion

_STRICT = ConfigDict(extra="forbid")


class Credentials(BaseModel):
    model_config = _STRICT
    username: str = Field(min_length=1)
    # Always a reference, resolved from the environment at runtime by eagleview.safety.secrets.
    password: str = Field(pattern=r"^secret://[a-z][a-z0-9_]*$")


class TenantProfile(BaseModel):
    model_config = _STRICT
    schema_version: SchemaVersion = "1.0"
    slug: Name
    name: str
    app_profile: Name
    base_url: str = Field(pattern=r"^https?://[^/@\s]+$")  # an origin: no path, no userinfo
    credentials: Credentials

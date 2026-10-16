"""The Anthropic client for authoring-time calls. The key comes from the merged environment (the
shell over `.env`), is registered with the redactor as a secret, and is never logged."""

from collections.abc import Mapping

import anthropic
import httpx2

from eagleview.safety.redaction import Redactor

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_EFFORT = "medium"


class DiscoveryUnavailable(Exception):
    pass


def model_from(environ: Mapping[str, str], override: str | None = None) -> str:
    return override or environ.get("EAGLEVIEW_MODEL") or DEFAULT_MODEL


def make_client(
    environ: Mapping[str, str],
    redactor: Redactor,
    *,
    http_client: httpx2.AsyncClient | None = None,
) -> anthropic.AsyncAnthropic:
    key = environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise DiscoveryUnavailable("discovery needs ANTHROPIC_API_KEY (in the shell or .env)")
    redactor.register_secret(key)
    workspace = environ.get("ANTHROPIC_WORKSPACE_ID")
    return anthropic.AsyncAnthropic(
        api_key=key,
        default_headers={"anthropic-workspace-id": workspace} if workspace else None,
        http_client=http_client,
        max_retries=2,
    )

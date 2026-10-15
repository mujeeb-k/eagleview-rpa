"""Secrets by reference. Artifacts, prompts and the repo only ever hold `secret://<name>`; the value
comes from the environment variable `<NAME>` at runtime and is registered with the redactor the
moment it is resolved, so it can never be written to disk."""

import os
import re
from collections.abc import Callable, Mapping

_REF = re.compile(r"^secret://([a-z][a-z0-9_]*)$")


class SecretMissing(LookupError):
    """The environment variable behind a secret reference is not set. Its value is never shown."""


def is_secret_ref(value: str) -> bool:
    return _REF.fullmatch(value) is not None


def secret_name(ref: str) -> str:
    match = _REF.fullmatch(ref)
    if match is None:
        raise ValueError(f"not a secret reference: {ref!r}")
    return match.group(1)


def env_var(ref: str) -> str:
    """`secret://lantern_bay_teller_password` -> `LANTERN_BAY_TELLER_PASSWORD`."""
    return secret_name(ref).upper()


class SecretResolver:
    def __init__(
        self,
        environ: Mapping[str, str] | None = None,
        on_resolve: Callable[[str], None] | None = None,
    ) -> None:
        self._environ = os.environ if environ is None else environ
        self._on_resolve = on_resolve

    def resolve(self, ref: str) -> str:
        name = env_var(ref)
        value = self._environ.get(name)
        if not value:
            raise SecretMissing(f"{ref} needs the environment variable {name}")
        if self._on_resolve is not None:
            self._on_resolve(value)
        return value

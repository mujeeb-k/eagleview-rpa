"""`python -m mock_bank [slug ...]`: serve the tenants (default: all) on their configured ports."""

import asyncio
import contextlib
import os
import secrets
import sys
from pathlib import Path

from dotenv import load_dotenv

from mock_bank.config import Tenant, all_tenants, load_tenant
from mock_bank.server import PortInUse, serve


def ensure_passwords(tenants: list[Tenant], env_file: Path) -> dict[str, str]:
    """Each tenant's password from the environment.

    A missing one is generated and appended to `env_file` (gitignored), so a fresh clone needs no
    setup and the harness later reads the same value.
    """
    passwords: dict[str, str] = {}
    added: list[str] = []
    for t in tenants:
        value = os.environ.get(t.password_env, "")
        if not value:
            value = secrets.token_urlsafe(18)
            os.environ[t.password_env] = value
            added.append(f"{t.password_env}={value}")
        passwords[t.slug] = value
    if added:
        existing = env_file.read_text() if env_file.exists() else ""
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        with env_file.open("a") as f:
            f.write(prefix + "\n".join(added) + "\n")
        names = ", ".join(line.split("=", 1)[0] for line in added)
        print(f"Generated {names} and saved to {env_file}.", flush=True)
    return passwords


def main(argv: list[str]) -> int:
    env_file = Path.cwd() / ".env"
    load_dotenv(env_file, override=False)
    tenants = [load_tenant(slug) for slug in argv] if argv else all_tenants()
    passwords = ensure_passwords(tenants, env_file)
    for t in tenants:
        print(
            f"{t.short_name}: sign on as {t.username} with ${t.password_env} from .env", flush=True
        )
    try:
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(serve(tenants, passwords))
    except PortInUse as e:
        port = e.tenant.port
        print(
            f"Port {port} ({e.tenant.short_name}) is already in use, maybe by another `make bank`. "
            f"Find the process with: lsof -nP -iTCP:{port} -sTCP:LISTEN",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

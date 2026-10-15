"""`python -m mock_bank [slug ...]`: serve the tenants (default: all) on their configured ports."""

import asyncio
import contextlib
import os
import sys

from dotenv import load_dotenv

from mock_bank.config import PASSWORD_ENV, all_tenants, load_tenant
from mock_bank.server import serve


def main(argv: list[str]) -> int:
    load_dotenv(override=False)
    password = os.environ.get(PASSWORD_ENV, "")
    if not password:
        print(
            f"{PASSWORD_ENV} is not set. Put a training password of your choice in .env "
            "(see .env.example); the harness reads the same variable.",
            file=sys.stderr,
        )
        return 2
    tenants = [load_tenant(slug) for slug in argv] if argv else all_tenants()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve(tenants, password))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

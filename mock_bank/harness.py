"""Running the mock bank from a harness (evidence scripts, demos): both tenants in-process on
ephemeral ports, with admin helpers. The passwords come from `.env`, generated if missing."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx2 as httpx
from dotenv import load_dotenv

from mock_bank.__main__ import ensure_passwords
from mock_bank.config import Tenant, all_tenants
from mock_bank.server import BankThread


@dataclass
class BankClient:
    tenant: Tenant
    url: str
    password: str

    def _admin(self) -> httpx.Client:
        return httpx.Client(base_url=self.url, timeout=10)

    def reset(self) -> None:
        """Seeded data back, sessions dropped, faults cleared."""
        with self._admin() as c:
            c.post("/__admin/reset").raise_for_status()

    def set_faults(self, **specs: str) -> None:
        with self._admin() as c:
            r = c.post("/__admin/faults", json=specs)
            r.raise_for_status()

    def share_rows(self, member: str) -> int:
        """Share accounts the bank shows for a member, read over HTTP like a teller would."""
        with httpx.Client(base_url=self.url, follow_redirects=True, timeout=10) as c:
            c.post("/signon", data={"f1": self.tenant.username, "f2": self.password})
            if self.tenant.select_branch:
                c.post("/branch", data={"br": self.tenant.branches[0]})
            page = c.post("/inq", data={"txtA": member, "txtB": ""}).text
        section = page.split("Share Accounts", 1)[1].split("Loan Accounts", 1)[0]
        return len(re.findall(r"<tr>\s*<td><font face=\"Courier New\"", section))


@contextmanager
def running_banks(env_file: Path = Path(".env")) -> Iterator[dict[str, BankClient]]:
    load_dotenv(env_file, override=False)
    tenants = all_tenants()
    passwords = ensure_passwords(tenants, env_file)
    thread = BankThread(tenants, passwords).start()
    try:
        yield {
            slug: BankClient(r.tenant, r.url, passwords[slug]) for slug, r in thread.running.items()
        }
    finally:
        thread.stop()

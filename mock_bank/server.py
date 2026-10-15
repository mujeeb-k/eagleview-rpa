"""Serve one or more tenants from a single process, in the foreground or on a background thread."""

import asyncio
import socket
import threading
from dataclasses import dataclass

import uvicorn

from mock_bank.app import create_app
from mock_bank.config import Tenant

HOST = "127.0.0.1"


@dataclass
class Running:
    tenant: Tenant
    url: str


def _server(tenant: Tenant, password: str) -> uvicorn.Server:
    config = uvicorn.Config(create_app(tenant, password), log_level="warning", access_log=False)
    return uvicorn.Server(config)


def _bind(port: int) -> socket.socket:
    # Bound and listening before uvicorn starts, so early connections queue instead of racing.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, port))
    sock.listen(128)
    return sock


async def serve(tenants: list[Tenant], passwords: dict[str, str]) -> None:
    """Serve every tenant on its configured port until interrupted. Passwords are keyed by slug."""
    pairs = [(_server(t, passwords[t.slug]), _bind(t.port)) for t in tenants]
    for t in tenants:
        print(f"Ironbridge Teller v{t.version} ({t.name}): http://{HOST}:{t.port}/", flush=True)
    await asyncio.gather(*(srv.serve(sockets=[sock]) for srv, sock in pairs))


class BankThread:
    """Every tenant on an ephemeral port, on a daemon thread. Used by tests and the harness."""

    def __init__(self, tenants: list[Tenant], passwords: dict[str, str]) -> None:
        self._pairs = [(_server(t, passwords[t.slug]), _bind(0)) for t in tenants]
        self.running = {
            t.slug: Running(t, f"http://{HOST}:{sock.getsockname()[1]}")
            for t, (_, sock) in zip(tenants, self._pairs, strict=True)
        }
        self._thread = threading.Thread(target=self._run, daemon=True, name="mock-bank")

    async def _serve(self) -> None:
        await asyncio.gather(*(srv.serve(sockets=[sock]) for srv, sock in self._pairs))

    def _run(self) -> None:
        asyncio.run(self._serve())

    def start(self) -> "BankThread":
        self._thread.start()
        return self

    def stop(self) -> None:
        for srv, _ in self._pairs:
            srv.should_exit = True
        self._thread.join(timeout=10)
        for _, sock in self._pairs:
            sock.close()

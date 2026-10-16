"""Serving the console inside the runner's process and event loop, next to the live session it
controls (PLAN §2). The socket is bound before uvicorn starts, so a busy port fails at once with a
clear error, and early connections queue instead of racing startup."""

import asyncio
import contextlib
import socket
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

DEFAULT_PORT = 8700


class ConsolePortInUse(OSError):
    pass


@asynccontextmanager
async def console_server(
    app: FastAPI, *, port: int = DEFAULT_PORT, host: str = "127.0.0.1"
) -> AsyncGenerator[str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
    except OSError as exc:
        sock.close()
        raise ConsolePortInUse(
            f"the operator console cannot listen on {host}:{port} ({exc.strerror}); "
            "pass --console-port to use another port"
        ) from None
    sock.listen()
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=2))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        yield f"http://{host}:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        with contextlib.suppress(asyncio.CancelledError):
            await task
        sock.close()

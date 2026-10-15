"""`make bank` start-up: a busy port gives one friendly line and a non-zero exit, no traceback."""

import socket
from collections.abc import Iterator
from pathlib import Path

import pytest

import mock_bank.__main__ as bank_main
from mock_bank.config import Tenant, all_tenants
from mock_bank.server import HOST, PortInUse, bind_all


@pytest.fixture
def busy_port() -> Iterator[int]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((HOST, 0))
    sock.listen(1)
    yield sock.getsockname()[1]
    sock.close()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def _tenants(*ports: int) -> list[Tenant]:
    return [t.model_copy(update={"port": p}) for t, p in zip(all_tenants(), ports, strict=True)]


def test_bind_all_names_the_busy_tenant_and_releases_the_rest(busy_port: int) -> None:
    free = _free_port()
    tenants = _tenants(free, busy_port)
    with pytest.raises(PortInUse) as info:
        bind_all(tenants)
    assert info.value.tenant.port == busy_port
    # The port bound before the failure was released, so a retry can use it.
    for sock in bind_all(_tenants(free, _free_port())):
        sock.close()


def test_main_prints_one_line_and_exits_non_zero(
    busy_port: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)  # any generated passwords land in a throwaway .env
    monkeypatch.setattr(bank_main, "all_tenants", lambda: _tenants(busy_port, _free_port()))
    assert bank_main.main([]) == 1
    err = capsys.readouterr().err.strip()
    assert err.count("\n") == 0
    assert f"Port {busy_port}" in err
    assert f"lsof -nP -iTCP:{busy_port} -sTCP:LISTEN" in err
    assert "Traceback" not in err

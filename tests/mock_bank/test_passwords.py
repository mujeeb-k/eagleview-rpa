"""`make bank`: missing tenant passwords are generated into .env, never committed."""

from pathlib import Path

import pytest

from mock_bank.__main__ import ensure_passwords
from mock_bank.config import all_tenants

ENV_NAMES = ["COPPERHILL_TELLER_PASSWORD", "LANTERN_BAY_TELLER_PASSWORD"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_env_names_follow_the_secret_refs() -> None:
    assert sorted(t.password_env for t in all_tenants()) == ENV_NAMES


def test_missing_passwords_are_generated_and_appended(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=")  # no trailing newline: must not be glued to
    passwords = ensure_passwords(all_tenants(), env)

    lines = env.read_text().splitlines()
    assert lines[0] == "ANTHROPIC_API_KEY="
    written = dict(line.split("=", 1) for line in lines[1:])
    assert sorted(written) == ENV_NAMES
    assert len(set(written.values())) == 2 and all(len(v) >= 20 for v in written.values())
    assert {passwords[t.slug] for t in all_tenants()} == set(written.values())
    out = capsys.readouterr().out
    assert "LANTERN_BAY_TELLER_PASSWORD" in out
    assert not any(v in out for v in written.values())  # the user is told, the value is not shown


def test_existing_passwords_are_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    monkeypatch.setenv("LANTERN_BAY_TELLER_PASSWORD", "already-set")
    passwords = ensure_passwords(all_tenants(), env)
    assert passwords["lantern-bay"] == "already-set"
    assert env.read_text().startswith("COPPERHILL_TELLER_PASSWORD=")
    assert "LANTERN_BAY" not in env.read_text()
    # A second run finds both and writes nothing.
    before = env.read_text()
    ensure_passwords(all_tenants(), env)
    assert env.read_text() == before


def test_env_file_is_gitignored() -> None:
    ignored = (Path(__file__).parents[2] / ".gitignore").read_text().splitlines()
    assert ".env" in ignored

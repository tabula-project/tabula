"""End-to-end tests for the ``tabula servers`` Typer subcommand surface.

Drives the canonical Typer root ``tabula_cli.main:app`` via Typer's
``CliRunner`` so we exercise command wiring as well as the handlers. The
pinning store is redirected to ``tmp_path`` via ``TABULA_CONFIG_DIR``
(same mechanism the wire-side unit tests use).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from tabula_cli.main import app
from tabula_wire.client import pinning


PK_HEX = "aa" * 32
PK_HEX_2 = "bb" * 32

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "tabula-cfg"
    monkeypatch.setenv("TABULA_CONFIG_DIR", str(cfg))
    return cfg


# ---------- add -----------------------------------------------------------------


def test_servers_add_then_list(isolated_config_dir: Path) -> None:
    r = runner.invoke(app, ["servers", "add", "alpha", "alpha.local:7777", PK_HEX])
    assert r.exit_code == 0, r.stderr
    assert "pinned alpha" in r.stdout
    assert "alpha.local:7777" in r.stdout

    # Entry actually persisted.
    e = pinning.lookup("alpha")
    assert e is not None
    assert e.host == "alpha.local"
    assert e.port == 7777
    assert e.pubkey_hex == PK_HEX

    # List prints the entry in a table.
    r2 = runner.invoke(app, ["servers", "list"])
    assert r2.exit_code == 0, r2.stderr
    assert "alpha" in r2.stdout
    assert "alpha.local:7777" in r2.stdout
    assert PK_HEX in r2.stdout


def test_servers_add_duplicate_refused() -> None:
    r1 = runner.invoke(app, ["servers", "add", "alpha", "h:1", PK_HEX])
    assert r1.exit_code == 0, r1.stderr
    r2 = runner.invoke(app, ["servers", "add", "alpha", "h:1", PK_HEX_2])
    assert r2.exit_code == 1
    err = r2.stderr
    assert "already pinned" in err or "duplicate" in err.lower() or "alpha" in err


def test_servers_add_force_replaces() -> None:
    r1 = runner.invoke(app, ["servers", "add", "alpha", "old:1", PK_HEX])
    assert r1.exit_code == 0
    r2 = runner.invoke(
        app, ["servers", "add", "alpha", "new:2", PK_HEX_2, "--force"]
    )
    assert r2.exit_code == 0, r2.stderr
    e = pinning.lookup("alpha")
    assert e is not None
    assert e.host == "new"
    assert e.port == 2
    assert e.pubkey_hex == PK_HEX_2


def test_servers_add_invalid_endpoint() -> None:
    r = runner.invoke(app, ["servers", "add", "alpha", "not-an-endpoint", PK_HEX])
    assert r.exit_code == 2
    assert "host:port" in r.stderr


def test_servers_add_invalid_pubkey() -> None:
    r = runner.invoke(app, ["servers", "add", "alpha", "h:1", "deadbeef"])
    assert r.exit_code == 2
    assert "hex" in r.stderr.lower() or "64" in r.stderr


def test_servers_add_invalid_port() -> None:
    r = runner.invoke(app, ["servers", "add", "alpha", "h:99999", PK_HEX])
    assert r.exit_code == 2
    assert "port" in r.stderr.lower()


# ---------- list ----------------------------------------------------------------


def test_servers_list_empty(isolated_config_dir: Path) -> None:
    r = runner.invoke(app, ["servers", "list"])
    assert r.exit_code == 0, r.stderr
    assert "no pinned servers" in r.stdout


# ---------- remove --------------------------------------------------------------


def test_servers_remove_round_trip() -> None:
    runner.invoke(app, ["servers", "add", "alpha", "h:1", PK_HEX])
    r = runner.invoke(app, ["servers", "remove", "alpha"])
    assert r.exit_code == 0, r.stderr
    assert "removed alpha" in r.stdout
    assert pinning.lookup("alpha") is None


def test_servers_remove_unknown() -> None:
    r = runner.invoke(app, ["servers", "remove", "never-pinned"])
    assert r.exit_code == 1
    assert "never-pinned" in r.stderr


# ---------- Typer wiring -------------------------------------------------------


def test_servers_no_action_shows_help() -> None:
    """``tabula servers`` with no action exits non-zero (Typer no_args_is_help)."""
    r = runner.invoke(app, ["servers"])
    assert r.exit_code != 0


def test_top_level_no_command_shows_help() -> None:
    """``tabula`` with no command exits non-zero."""
    r = runner.invoke(app, [])
    assert r.exit_code != 0

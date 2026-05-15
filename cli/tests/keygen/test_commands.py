"""Tests for the ``tabula keygen`` CLI subcommand.

Originally landed under ``wire/tests/cli/test_keygen.py`` in PR #46;
relocated here per architect proposal #57 / follow-up #68 alongside the
move of the keygen CLI from ``tabula-wire`` to ``tabula-cli``.

The driver was ported from argparse to Typer in the same move; tests
now invoke the canonical ``tabula_cli.main:app`` entrypoint via
:class:`typer.testing.CliRunner` instead of calling an argparse ``main``
directly. The user-visible surface (flags, defaults, exit codes,
stdout/stderr lines) is preserved verbatim.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tabula_cli.keygen.commands import default_key_path
from tabula_cli.main import app as tabula_app
from tabula_wire.crypto.keys import KEY_FILE_MAGIC, load_secret_key


# A single shared runner. Typer's CliRunner (>=0.25) keeps stdout and
# stderr separate by default, so tests can assert on the public-key
# lines (stdout) and status/error lines (stderr) independently via
# ``result.stdout`` / ``result.stderr``.
runner = CliRunner()


# ---------------------------------------------------------------------------
# Default-path resolution
# ---------------------------------------------------------------------------


def test_default_path_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    p = default_key_path("client")
    assert p == tmp_path / ".config" / "tabula" / "client_key"


def test_default_path_server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    p = default_key_path("server")
    assert p == tmp_path / ".config" / "tabula" / "server_key"


def test_default_path_respects_xdg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    p = default_key_path("client")
    assert p == tmp_path / "xdg" / "tabula" / "client_key"


def test_default_path_rejects_unknown_role() -> None:
    with pytest.raises(ValueError):
        default_key_path("nope")


# ---------------------------------------------------------------------------
# Generate flow
# ---------------------------------------------------------------------------


def test_keygen_generate_writes_file(tmp_path: Path) -> None:
    target = tmp_path / "client_key"
    result = runner.invoke(
        tabula_app, ["keygen", "--out", str(target), "--role", "client"]
    )
    assert result.exit_code == 0, (result.stdout, result.stderr)

    # Public-key line on stdout, status on stderr.
    assert "tabula client public key: " in result.stdout
    pub_match = re.search(
        r"tabula client public key: ([0-9a-f]{64})", result.stdout
    )
    assert pub_match is not None, result.stdout

    assert target.exists()
    text = target.read_text(encoding="utf-8")
    assert text.startswith(f"{KEY_FILE_MAGIC}\n")

    # Cross-check: the printed pubkey matches what the loader derives.
    secret = load_secret_key(target)
    assert secret.public_key().hex() == pub_match.group(1)


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode check")
def test_keygen_generate_sets_0600_mode(tmp_path: Path) -> None:
    target = tmp_path / "key"
    result = runner.invoke(tabula_app, ["keygen", "--out", str(target)])
    assert result.exit_code == 0, (result.stdout, result.stderr)
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_keygen_no_print_pub_suppresses_pubkey(tmp_path: Path) -> None:
    target = tmp_path / "key"
    result = runner.invoke(
        tabula_app, ["keygen", "--out", str(target), "--no-print-pub"]
    )
    assert result.exit_code == 0, (result.stdout, result.stderr)

    assert "public key" not in result.stdout
    # stderr still gets the "wrote secret key" status line.
    assert "wrote secret key" in result.stderr


def test_keygen_role_server_prints_server_label(tmp_path: Path) -> None:
    target = tmp_path / "key"
    result = runner.invoke(
        tabula_app, ["keygen", "--out", str(target), "--role", "server"]
    )
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert "tabula server public key: " in result.stdout
    assert "client public key" not in result.stdout


def test_keygen_refuses_overwrite_without_force(tmp_path: Path) -> None:
    target = tmp_path / "key"
    first = runner.invoke(tabula_app, ["keygen", "--out", str(target)])
    assert first.exit_code == 0, (first.stdout, first.stderr)
    original = target.read_bytes()

    second = runner.invoke(tabula_app, ["keygen", "--out", str(target)])
    assert second.exit_code == 1

    assert "refusing to overwrite" in second.stderr

    # Original content untouched.
    assert target.read_bytes() == original


def test_keygen_overwrites_with_force(tmp_path: Path) -> None:
    target = tmp_path / "key"
    first = runner.invoke(tabula_app, ["keygen", "--out", str(target)])
    assert first.exit_code == 0, (first.stdout, first.stderr)
    first_bytes = target.read_bytes()

    second = runner.invoke(
        tabula_app, ["keygen", "--out", str(target), "--force"]
    )
    assert second.exit_code == 0, (second.stdout, second.stderr)
    second_bytes = target.read_bytes()

    assert first_bytes != second_bytes  # two separately-generated keys
    assert second_bytes.startswith(KEY_FILE_MAGIC.encode("utf-8") + b"\n")


def test_keygen_creates_missing_parent_dir(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "config" / "client_key"
    result = runner.invoke(tabula_app, ["keygen", "--out", str(target)])
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert target.exists()


def test_keygen_uses_default_path_when_out_omitted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    expected = tmp_path / ".config" / "tabula" / "client_key"

    result = runner.invoke(tabula_app, ["keygen", "--role", "client"])
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert expected.exists()

    assert str(expected) in result.stderr


# ---------------------------------------------------------------------------
# Show flow (read-only)
# ---------------------------------------------------------------------------


def test_keygen_show_round_trips_pubkey(tmp_path: Path) -> None:
    target = tmp_path / "key"

    # Generate; capture the printed pubkey.
    gen = runner.invoke(
        tabula_app, ["keygen", "--out", str(target), "--role", "client"]
    )
    assert gen.exit_code == 0, (gen.stdout, gen.stderr)
    gen_pub = re.search(
        r"tabula client public key: ([0-9a-f]{64})", gen.stdout
    )
    assert gen_pub is not None

    # Show; same pubkey, no file modification.
    mtime_before = target.stat().st_mtime_ns
    contents_before = target.read_bytes()

    show = runner.invoke(
        tabula_app, ["keygen", "show", str(target), "--role", "client"]
    )
    assert show.exit_code == 0, (show.stdout, show.stderr)
    show_pub = re.search(
        r"tabula client public key: ([0-9a-f]{64})", show.stdout
    )
    assert show_pub is not None

    assert show_pub.group(1) == gen_pub.group(1)
    assert target.read_bytes() == contents_before
    assert target.stat().st_mtime_ns == mtime_before


def test_keygen_show_without_role_uses_generic_label(tmp_path: Path) -> None:
    target = tmp_path / "key"
    gen = runner.invoke(tabula_app, ["keygen", "--out", str(target)])
    assert gen.exit_code == 0, (gen.stdout, gen.stderr)

    show = runner.invoke(tabula_app, ["keygen", "show", str(target)])
    assert show.exit_code == 0, (show.stdout, show.stderr)
    assert (
        re.search(r"^tabula public key: [0-9a-f]{64}$", show.stdout.strip())
        is not None
    )


def test_keygen_show_reports_missing_file(tmp_path: Path) -> None:
    result = runner.invoke(
        tabula_app, ["keygen", "show", str(tmp_path / "nope")]
    )
    assert result.exit_code == 1
    assert "does not exist" in result.stderr


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode check")
def test_keygen_show_refuses_loose_mode(tmp_path: Path) -> None:
    target = tmp_path / "key"
    gen = runner.invoke(tabula_app, ["keygen", "--out", str(target)])
    assert gen.exit_code == 0, (gen.stdout, gen.stderr)

    target.chmod(0o644)
    result = runner.invoke(tabula_app, ["keygen", "show", str(target)])
    assert result.exit_code == 1
    assert "insecure permissions" in result.stderr


# ---------------------------------------------------------------------------
# Typer plumbing
# ---------------------------------------------------------------------------


def test_keygen_help_runs() -> None:
    """``tabula keygen --help`` lists the generate-side flags."""
    result = runner.invoke(tabula_app, ["keygen", "--help"])
    assert result.exit_code == 0
    # Strip ANSI color codes — Typer/Rich injects them and splits flag names
    # like "\x1b[1;36m-\x1b[0m\x1b[1;36m-role\x1b[0m" so substring checks
    # against contiguous "--role" fail. Same fix as test_up_help_lists_flags.
    import re
    out = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
    assert "X25519" in out
    assert "--role" in out
    assert "--force" in out


def test_top_level_help_runs() -> None:
    """``tabula --help`` advertises the ``keygen`` subcommand group."""
    result = runner.invoke(tabula_app, ["--help"])
    assert result.exit_code == 0
    assert "keygen" in result.stdout

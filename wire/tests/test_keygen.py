"""Tests for the ``tabula keygen`` CLI subcommand."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

import pytest

from tabula_wire.cli import main as cli_main
from tabula_wire.cli.keygen import default_key_path
from tabula_wire.crypto.keys import KEY_FILE_MAGIC, load_secret_key


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


def test_keygen_generate_writes_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "client_key"
    rc = cli_main(["keygen", "--out", str(target), "--role", "client"])
    assert rc == 0

    captured = capsys.readouterr()
    # Public-key line on stdout, status on stderr.
    assert "tabula client public key: " in captured.out
    pub_match = re.search(r"tabula client public key: ([0-9a-f]{64})", captured.out)
    assert pub_match is not None, captured.out

    assert target.exists()
    text = target.read_text(encoding="utf-8")
    assert text.startswith(f"{KEY_FILE_MAGIC}\n")

    # Cross-check: the printed pubkey matches what the loader derives.
    secret = load_secret_key(target)
    assert secret.public_key().hex() == pub_match.group(1)


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode check")
def test_keygen_generate_sets_0600_mode(tmp_path: Path) -> None:
    target = tmp_path / "key"
    rc = cli_main(["keygen", "--out", str(target)])
    assert rc == 0
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_keygen_no_print_pub_suppresses_pubkey(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "key"
    rc = cli_main(["keygen", "--out", str(target), "--no-print-pub"])
    assert rc == 0

    captured = capsys.readouterr()
    assert "public key" not in captured.out
    # stderr still gets the "wrote secret key" status line.
    assert "wrote secret key" in captured.err


def test_keygen_role_server_prints_server_label(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "key"
    rc = cli_main(["keygen", "--out", str(target), "--role", "server"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "tabula server public key: " in captured.out
    assert "client public key" not in captured.out


def test_keygen_refuses_overwrite_without_force(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "key"
    assert cli_main(["keygen", "--out", str(target)]) == 0
    original = target.read_bytes()

    rc = cli_main(["keygen", "--out", str(target)])
    assert rc == 1

    captured = capsys.readouterr()
    assert "refusing to overwrite" in captured.err

    # Original content untouched.
    assert target.read_bytes() == original


def test_keygen_overwrites_with_force(tmp_path: Path) -> None:
    target = tmp_path / "key"
    assert cli_main(["keygen", "--out", str(target)]) == 0
    first = target.read_bytes()

    assert cli_main(["keygen", "--out", str(target), "--force"]) == 0
    second = target.read_bytes()

    assert first != second  # two separately-generated keys
    assert second.startswith(KEY_FILE_MAGIC.encode("utf-8") + b"\n")


def test_keygen_creates_missing_parent_dir(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "config" / "client_key"
    rc = cli_main(["keygen", "--out", str(target)])
    assert rc == 0
    assert target.exists()


def test_keygen_uses_default_path_when_out_omitted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    expected = tmp_path / ".config" / "tabula" / "client_key"

    rc = cli_main(["keygen", "--role", "client"])
    assert rc == 0
    assert expected.exists()

    captured = capsys.readouterr()
    assert str(expected) in captured.err


# ---------------------------------------------------------------------------
# Show flow (read-only)
# ---------------------------------------------------------------------------


def test_keygen_show_round_trips_pubkey(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "key"

    # Generate; capture the printed pubkey.
    assert cli_main(["keygen", "--out", str(target), "--role", "client"]) == 0
    gen_out = capsys.readouterr().out
    gen_pub = re.search(r"tabula client public key: ([0-9a-f]{64})", gen_out)
    assert gen_pub is not None

    # Show; same pubkey, no file modification.
    mtime_before = target.stat().st_mtime_ns
    contents_before = target.read_bytes()

    assert cli_main(["keygen", "show", str(target), "--role", "client"]) == 0
    show_out = capsys.readouterr().out
    show_pub = re.search(r"tabula client public key: ([0-9a-f]{64})", show_out)
    assert show_pub is not None

    assert show_pub.group(1) == gen_pub.group(1)
    assert target.read_bytes() == contents_before
    assert target.stat().st_mtime_ns == mtime_before


def test_keygen_show_without_role_uses_generic_label(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "key"
    assert cli_main(["keygen", "--out", str(target)]) == 0
    capsys.readouterr()  # discard

    assert cli_main(["keygen", "show", str(target)]) == 0
    out = capsys.readouterr().out
    assert re.search(r"^tabula public key: [0-9a-f]{64}$", out.strip()) is not None


def test_keygen_show_reports_missing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli_main(["keygen", "show", str(tmp_path / "nope")])
    assert rc == 1
    assert "does not exist" in capsys.readouterr().err


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode check")
def test_keygen_show_refuses_loose_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "key"
    assert cli_main(["keygen", "--out", str(target)]) == 0
    capsys.readouterr()

    target.chmod(0o644)
    rc = cli_main(["keygen", "show", str(target)])
    assert rc == 1
    assert "insecure permissions" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# argparse plumbing
# ---------------------------------------------------------------------------


def test_keygen_help_runs(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli_main(["keygen", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "X25519" in out
    assert "--role" in out
    assert "--force" in out


def test_top_level_help_runs(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli_main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "keygen" in out

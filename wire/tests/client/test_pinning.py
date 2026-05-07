"""Tests for the client-side server pubkey pinning store.

Acceptance-criteria coverage map (from issue #32):

* "Add → list → lookup → remove round trip"               -> test_round_trip
* "Add to nonexistent file creates parent dir 0700,
   file with 0600"                                        -> test_creates_parent_dir_and_file_modes
* "Refuse to add duplicate label without --force"         -> test_duplicate_label_refused
* "Malformed lines are skipped with a warning, not crash" -> test_malformed_lines_skipped
* "Lookup of missing label returns None"                  -> test_lookup_missing_returns_none
* "File format ... line oriented; header on create"       -> test_header_prepended_on_create,
                                                            test_file_format_round_trip
* "Mismatch error message includes pinned + offered key"  -> test_mismatch_error_message
* "TOFU refuse-and-suggest"                               -> test_refuse_unknown_label_message
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

import pytest

from tabula_wire.client import pinning
from tabula_wire.client.exceptions import (
    DuplicateLabelError,
    InvalidPubkeyError,
    UnknownLabelError,
)
from tabula_wire.client.pinning import ServerEntry


# 32-byte test pubkeys (deterministic, not from a real keypair — fine for
# pinning-store tests since we never run a handshake here).
PK_A = bytes.fromhex("aa" * 32)
PK_B = bytes.fromhex("bb" * 32)
PK_C = bytes.fromhex("cc" * 32)


@pytest.fixture(autouse=True)
def isolated_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the pinning store under tmp_path via TABULA_CONFIG_DIR.

    Every test gets a clean directory; the store file is *not* pre-created
    so we can also exercise the "create on first write" path.
    """
    cfg = tmp_path / "tabula-cfg"
    monkeypatch.setenv("TABULA_CONFIG_DIR", str(cfg))
    return cfg


# ---------- core round trip -----------------------------------------------------


def test_round_trip(isolated_config_dir: Path) -> None:
    """add -> list -> lookup -> remove on a fresh store."""
    entry = pinning.add("alpha", "alpha.local", 7777, PK_A)
    assert entry.label == "alpha"
    assert entry.host == "alpha.local"
    assert entry.port == 7777
    assert entry.pubkey == PK_A

    listed = pinning.list_all()
    assert listed == [entry]

    looked = pinning.lookup("alpha")
    assert looked == entry

    removed = pinning.remove("alpha")
    assert removed == entry
    assert pinning.list_all() == []
    assert pinning.lookup("alpha") is None


def test_round_trip_multiple_entries(isolated_config_dir: Path) -> None:
    """Multiple entries preserve insertion order and round-trip."""
    e1 = pinning.add("alpha", "a.example", 1111, PK_A)
    e2 = pinning.add("beta", "b.example", 2222, PK_B)
    e3 = pinning.add("gamma", "g.example", 3333, PK_C)

    assert pinning.list_all() == [e1, e2, e3]

    pinning.remove("beta")
    assert [e.label for e in pinning.list_all()] == ["alpha", "gamma"]


# ---------- file/dir creation + permissions ------------------------------------


def test_creates_parent_dir_and_file_modes(isolated_config_dir: Path) -> None:
    """First add creates parent dir with 0700 and file with 0600."""
    assert not isolated_config_dir.exists()

    pinning.add("alpha", "host", 1234, PK_A)

    # Parent dir was created with 0700.
    dir_mode = stat.S_IMODE(isolated_config_dir.stat().st_mode)
    assert dir_mode == 0o700, f"expected dir mode 0700, got {oct(dir_mode)}"

    # File was created with 0600.
    file_path = isolated_config_dir / "known_servers"
    assert file_path.exists()
    file_mode = stat.S_IMODE(file_path.stat().st_mode)
    assert file_mode == 0o600, f"expected file mode 0600, got {oct(file_mode)}"


def test_header_prepended_on_create(isolated_config_dir: Path) -> None:
    """First write prepends the documentation header; subsequent writes keep it."""
    pinning.add("alpha", "host", 1234, PK_A)
    text = (isolated_config_dir / "known_servers").read_text(encoding="utf-8")
    assert text.startswith("# tabula known_servers")
    assert "Format" in text  # header documents the format

    # Subsequent rewrites (remove path) keep a header in the file too.
    pinning.add("beta", "host", 1234, PK_B)
    pinning.remove("alpha")
    text2 = (isolated_config_dir / "known_servers").read_text(encoding="utf-8")
    assert text2.startswith("# tabula known_servers")


def test_file_format_round_trip(isolated_config_dir: Path) -> None:
    """The on-disk line format is exactly `<label> <host>:<port> <hex>`."""
    pinning.add("alpha", "alpha.local", 7777, PK_A)
    body = (isolated_config_dir / "known_servers").read_text(encoding="utf-8")
    # Strip header lines, keep only entry lines.
    entry_lines = [
        ln
        for ln in body.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    assert entry_lines == [f"alpha alpha.local:7777 {'aa' * 32}"]


def test_existing_file_without_header_still_round_trips(
    isolated_config_dir: Path,
) -> None:
    """A user-edited file with no header still parses + round-trips correctly."""
    isolated_config_dir.mkdir(parents=True, mode=0o700)
    path = isolated_config_dir / "known_servers"
    path.write_text(f"alpha alpha.local:7777 {'aa' * 32}\n", encoding="utf-8")
    os.chmod(path, 0o600)

    listed = pinning.list_all()
    assert len(listed) == 1
    assert listed[0].label == "alpha"
    assert listed[0].pubkey == PK_A

    # Add a second entry; the file should still be valid.
    pinning.add("beta", "b.example", 2222, PK_B)
    listed2 = pinning.list_all()
    assert {e.label for e in listed2} == {"alpha", "beta"}


# ---------- duplicate / force ---------------------------------------------------


def test_duplicate_label_refused(isolated_config_dir: Path) -> None:
    """Adding the same label twice without force=True raises and does not mutate."""
    pinning.add("alpha", "host", 1234, PK_A)
    with pytest.raises(DuplicateLabelError):
        pinning.add("alpha", "other-host", 9999, PK_B)

    # Original entry is untouched.
    looked = pinning.lookup("alpha")
    assert looked is not None
    assert looked.host == "host"
    assert looked.port == 1234
    assert looked.pubkey == PK_A


def test_force_replaces_existing_entry(isolated_config_dir: Path) -> None:
    """force=True replaces the entry; only one row remains."""
    pinning.add("alpha", "host", 1234, PK_A)
    pinning.add("alpha", "newhost", 4321, PK_B, force=True)

    listed = pinning.list_all()
    assert len(listed) == 1
    assert listed[0].host == "newhost"
    assert listed[0].port == 4321
    assert listed[0].pubkey == PK_B


# ---------- malformed-line handling --------------------------------------------


def test_malformed_lines_skipped(
    isolated_config_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Garbage lines log a warning and are skipped — no crash."""
    isolated_config_dir.mkdir(parents=True, mode=0o700)
    path = isolated_config_dir / "known_servers"
    body = "\n".join(
        [
            "# header comment",
            "",
            f"alpha alpha.local:7777 {'aa' * 32}",  # ok
            "this line has too few fields",  # malformed
            f"beta b.example:2222 {'gg' * 32}",  # malformed pubkey (non-hex)
            "BAD-LABEL!! host:1 " + ("aa" * 32),  # malformed label
            "delta nohostport " + ("cc" * 32),  # malformed host:port
            f"gamma g.example:3333 {'cc' * 32}",  # ok
            "  # indented comment",
            "",
        ]
    )
    path.write_text(body + "\n", encoding="utf-8")
    os.chmod(path, 0o600)

    with caplog.at_level(logging.WARNING, logger="tabula_wire.client.pinning"):
        listed = pinning.list_all()

    labels = [e.label for e in listed]
    assert labels == ["alpha", "gamma"]

    # At least one warning was emitted per bad line; the test only asserts
    # that warnings were emitted, not exact count, so the implementation is
    # free to refine messages.
    bad_warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(bad_warnings) >= 4


def test_lookup_missing_returns_none(isolated_config_dir: Path) -> None:
    """lookup() of an absent label returns None — not raises."""
    # File doesn't even exist yet.
    assert pinning.lookup("never-pinned") is None

    # Now with some pins, but still absent.
    pinning.add("alpha", "host", 1234, PK_A)
    assert pinning.lookup("missing") is None
    assert pinning.lookup("") is None
    # Type-defensive: non-str also returns None (silent), since lookup is
    # called from CLI parsers.
    assert pinning.lookup(None) is None  # type: ignore[arg-type]


# ---------- remove() error path -------------------------------------------------


def test_remove_unknown_raises(isolated_config_dir: Path) -> None:
    """remove() on a missing label raises UnknownLabelError (and KeyError)."""
    pinning.add("alpha", "host", 1234, PK_A)
    with pytest.raises(UnknownLabelError):
        pinning.remove("never-pinned")
    # Also a KeyError for callers that already handle that.
    with pytest.raises(KeyError):
        pinning.remove("never-pinned")


def test_remove_missing_file_raises(isolated_config_dir: Path) -> None:
    """remove() on an entirely missing store still raises UnknownLabelError."""
    with pytest.raises(UnknownLabelError):
        pinning.remove("alpha")


# ---------- pubkey validation --------------------------------------------------


def test_add_rejects_short_pubkey(isolated_config_dir: Path) -> None:
    """add() rejects pubkeys that aren't exactly 32 bytes."""
    with pytest.raises(InvalidPubkeyError):
        pinning.add("alpha", "host", 1234, b"\x01" * 16)


def test_add_rejects_non_bytes_pubkey(isolated_config_dir: Path) -> None:
    with pytest.raises(InvalidPubkeyError):
        pinning.add("alpha", "host", 1234, "aa" * 32)  # type: ignore[arg-type]


def test_add_rejects_invalid_label(isolated_config_dir: Path) -> None:
    """Whitespace / colon / hash in label is rejected."""
    for bad in ("a b", "has:colon", "has#hash", ""):
        with pytest.raises(ValueError):
            pinning.add(bad, "host", 1234, PK_A)


def test_add_rejects_out_of_range_port(isolated_config_dir: Path) -> None:
    for bad in (0, -1, 65536, 1_000_000):
        with pytest.raises(ValueError):
            pinning.add("alpha", "host", bad, PK_A)


# ---------- error message templates (TOFU + mismatch) -------------------------


def test_refuse_unknown_label_message_mentions_servers_add() -> None:
    """TOFU refusal text is single-sourced and mentions `tabula servers add`."""
    msg = pinning.refuse_unknown_label_message("brand-new")
    assert "brand-new" in msg
    assert "tabula servers add" in msg
    # Must NOT silently TOFU.
    assert "refuse" in msg.lower() or "refuses" in msg.lower()


def test_mismatch_error_message_includes_both_keys() -> None:
    """Mismatch text shows pinned + offered hex and warns before re-pin."""
    expected = bytes.fromhex("11" * 32)
    offered = bytes.fromhex("22" * 32)
    msg = pinning.mismatch_error_message("alpha", expected, offered)
    assert "alpha" in msg
    assert "11" * 32 in msg
    assert "22" * 32 in msg
    # Must caution before mutating the store.
    assert "verify" in msg.lower() or "out-of-band" in msg.lower()
    assert "tabula servers remove" in msg
    assert "tabula servers add" in msg


# ---------- ServerEntry invariants ---------------------------------------------


def test_server_entry_to_line_roundtrip() -> None:
    """ServerEntry.to_line is the on-disk format; pubkey_hex is lowercase."""
    e = ServerEntry(label="alpha", host="alpha.local", port=7777, pubkey=PK_A)
    assert e.pubkey_hex == "aa" * 32
    assert e.to_line() == f"alpha alpha.local:7777 {'aa' * 32}"


def test_server_entry_rejects_bad_invariants() -> None:
    with pytest.raises(ValueError):
        ServerEntry(label="bad label", host="h", port=1, pubkey=PK_A)
    with pytest.raises(ValueError):
        ServerEntry(label="alpha", host="", port=1, pubkey=PK_A)
    with pytest.raises(ValueError):
        ServerEntry(label="alpha", host="h", port=70000, pubkey=PK_A)
    with pytest.raises(ValueError):
        ServerEntry(label="alpha", host="h", port=1, pubkey=b"\x00" * 16)

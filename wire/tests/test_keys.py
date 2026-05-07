"""Tests for tabula_wire.crypto.keys — file format and round-trip semantics."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from tabula_wire.crypto.keys import (
    KEY_FILE_MAGIC,
    PUBLIC_KEY_BYTES,
    SECRET_KEY_BYTES,
    KeyFileError,
    PublicKey,
    SecretKey,
    generate_keypair,
    load_secret_key,
    public_key_hex,
    save_secret_key,
)


def test_generate_keypair_produces_correct_sizes() -> None:
    secret = generate_keypair()
    assert isinstance(secret, SecretKey)
    assert len(secret.raw) == SECRET_KEY_BYTES
    pub = secret.public_key()
    assert isinstance(pub, PublicKey)
    assert len(pub.raw) == PUBLIC_KEY_BYTES


def test_secret_key_hex_round_trip() -> None:
    secret = generate_keypair()
    s_hex = secret.hex()
    assert len(s_hex) == SECRET_KEY_BYTES * 2
    assert s_hex == s_hex.lower()
    assert bytes.fromhex(s_hex) == secret.raw


def test_public_key_hex_helper_matches_method() -> None:
    secret = generate_keypair()
    assert public_key_hex(secret) == secret.public_key().hex()
    assert len(public_key_hex(secret)) == PUBLIC_KEY_BYTES * 2


def test_secret_key_rejects_wrong_size() -> None:
    with pytest.raises(ValueError):
        SecretKey(raw=b"\x00" * 31)
    with pytest.raises(ValueError):
        SecretKey(raw=b"\x00" * 33)


def test_public_key_rejects_wrong_size() -> None:
    with pytest.raises(ValueError):
        PublicKey(raw=b"\x00" * 31)


def test_save_creates_file_with_0600_mode(tmp_path: Path) -> None:
    secret = generate_keypair()
    target = tmp_path / "key"
    save_secret_key(secret, target)

    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_save_creates_parent_directory(tmp_path: Path) -> None:
    secret = generate_keypair()
    target = tmp_path / "nested" / "deep" / "key"
    save_secret_key(secret, target)

    assert target.exists()
    assert target.parent.is_dir()


def test_save_writes_canonical_format(tmp_path: Path) -> None:
    secret = generate_keypair()
    target = tmp_path / "key"
    save_secret_key(secret, target)

    text = target.read_text(encoding="utf-8")
    lines = text.split("\n")

    # Exactly: [magic, hex, ""] (trailing newline → empty third element)
    assert len(lines) == 3
    assert lines[0] == KEY_FILE_MAGIC
    assert len(lines[1]) == SECRET_KEY_BYTES * 2
    assert lines[1] == lines[1].lower()
    assert bytes.fromhex(lines[1]) == secret.raw
    assert lines[2] == ""


def test_save_refuses_overwrite_by_default(tmp_path: Path) -> None:
    secret = generate_keypair()
    target = tmp_path / "key"
    save_secret_key(secret, target)

    other = generate_keypair()
    with pytest.raises(FileExistsError):
        save_secret_key(other, target)

    # Original content unchanged.
    loaded = load_secret_key(target)
    assert loaded.raw == secret.raw


def test_save_overwrites_when_requested(tmp_path: Path) -> None:
    first = generate_keypair()
    target = tmp_path / "key"
    save_secret_key(first, target)

    second = generate_keypair()
    save_secret_key(second, target, overwrite=True)

    loaded = load_secret_key(target)
    assert loaded.raw == second.raw
    assert loaded.raw != first.raw


def test_load_round_trip(tmp_path: Path) -> None:
    secret = generate_keypair()
    target = tmp_path / "key"
    save_secret_key(secret, target)

    loaded = load_secret_key(target)
    assert loaded.raw == secret.raw
    assert loaded.public_key().raw == secret.public_key().raw


def test_load_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(KeyFileError, match="does not exist"):
        load_secret_key(tmp_path / "nonexistent")


def test_load_rejects_directory(tmp_path: Path) -> None:
    with pytest.raises(KeyFileError, match="not a regular file"):
        load_secret_key(tmp_path)


def test_load_rejects_wrong_magic(tmp_path: Path) -> None:
    target = tmp_path / "key"
    target.write_text(
        "tabula-x25519-secret-v999\n" + "00" * 32 + "\n",
        encoding="utf-8",
    )
    target.chmod(0o600)
    with pytest.raises(KeyFileError, match="unknown key file magic"):
        load_secret_key(target)


def test_load_rejects_uppercase_hex(tmp_path: Path) -> None:
    target = tmp_path / "key"
    body = f"{KEY_FILE_MAGIC}\n" + "AB" * 32 + "\n"
    target.write_text(body, encoding="utf-8")
    target.chmod(0o600)
    with pytest.raises(KeyFileError, match="lowercase"):
        load_secret_key(target)


def test_load_rejects_short_hex(tmp_path: Path) -> None:
    target = tmp_path / "key"
    body = f"{KEY_FILE_MAGIC}\n" + "ab" * 31 + "\n"
    target.write_text(body, encoding="utf-8")
    target.chmod(0o600)
    with pytest.raises(KeyFileError, match="hex line has length"):
        load_secret_key(target)


def test_load_rejects_invalid_hex(tmp_path: Path) -> None:
    target = tmp_path / "key"
    body = f"{KEY_FILE_MAGIC}\n" + "zz" * 32 + "\n"
    target.write_text(body, encoding="utf-8")
    target.chmod(0o600)
    with pytest.raises(KeyFileError, match="invalid hex"):
        load_secret_key(target)


def test_load_rejects_extra_lines(tmp_path: Path) -> None:
    target = tmp_path / "key"
    body = f"{KEY_FILE_MAGIC}\n" + "ab" * 32 + "\nextra\n"
    target.write_text(body, encoding="utf-8")
    target.chmod(0o600)
    with pytest.raises(KeyFileError, match="exactly two lines"):
        load_secret_key(target)


def test_load_rejects_missing_trailing_newline(tmp_path: Path) -> None:
    target = tmp_path / "key"
    body = f"{KEY_FILE_MAGIC}\n" + "ab" * 32  # no final newline
    target.write_text(body, encoding="utf-8")
    target.chmod(0o600)
    with pytest.raises(KeyFileError, match="exactly two lines"):
        load_secret_key(target)


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only mode check")
def test_load_rejects_world_readable(tmp_path: Path) -> None:
    secret = generate_keypair()
    target = tmp_path / "key"
    save_secret_key(secret, target)

    target.chmod(0o644)
    with pytest.raises(KeyFileError, match="insecure permissions"):
        load_secret_key(target)


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only mode check")
def test_load_rejects_group_readable(tmp_path: Path) -> None:
    secret = generate_keypair()
    target = tmp_path / "key"
    save_secret_key(secret, target)

    target.chmod(0o640)
    with pytest.raises(KeyFileError, match="insecure permissions"):
        load_secret_key(target)


def test_load_accepts_loose_mode_when_opt_out(tmp_path: Path) -> None:
    secret = generate_keypair()
    target = tmp_path / "key"
    save_secret_key(secret, target)

    if os.name == "posix":
        target.chmod(0o644)

    loaded = load_secret_key(target, require_secure_mode=False)
    assert loaded.raw == secret.raw


def test_two_generate_calls_produce_distinct_keys() -> None:
    a = generate_keypair()
    b = generate_keypair()
    assert a.raw != b.raw


def test_to_x25519_objects_round_trip(tmp_path: Path) -> None:
    """The cryptography-library wrappers compose: derived pubkey matches."""
    secret = generate_keypair()
    priv_obj = secret.to_x25519()
    # Re-derive via the cryptography library directly.
    from cryptography.hazmat.primitives import serialization as _ser

    derived_pub = priv_obj.public_key().public_bytes(
        encoding=_ser.Encoding.Raw,
        format=_ser.PublicFormat.Raw,
    )
    assert derived_pub == secret.public_key().raw

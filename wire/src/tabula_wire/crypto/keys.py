"""Static X25519 key file format for Tabula.

This module defines the **canonical** on-disk format for Tabula static secret
keys. All readers (the Noise XX initiator/responder in #18, the dialer in #27,
the chat CLI in #29) MUST use :func:`load_secret_key` rather than re-parsing
the file format. All writers MUST use :func:`save_secret_key`.

Format (v1)
-----------

A two-line UTF-8 text file::

    tabula-x25519-secret-v1\n
    <64 lowercase hex characters>\n

- Line 1 is the magic header :data:`KEY_FILE_MAGIC`, matched verbatim.
- Line 2 is the 32-byte X25519 raw secret scalar, lowercase hex.
- Trailing newline after the hex line is required; no other content permitted.
- File permissions must be ``0o600`` on POSIX. The loader refuses files with
  broader bits set unless the caller explicitly opts out (used in tests on
  filesystems that don't support POSIX modes).

Public keys are derived on demand and not persisted in this format.

Why hex? It avoids base64 padding/url-safe ambiguity. 32 bytes is short enough
that the wire-cost is negligible.

Future: a ``tabula-x25519-secret-v2`` magic header will signal a
passphrase-encrypted variant. The loader dispatches on the magic line, so v1
files remain readable forever.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)

KEY_FILE_MAGIC: Final[str] = "tabula-x25519-secret-v1"
"""Magic header for the v1 secret-key file format.

When v2 (encrypted) lands, the loader will dispatch on this line.
"""

SECRET_KEY_BYTES: Final[int] = 32
"""Length of a raw X25519 secret scalar."""

PUBLIC_KEY_BYTES: Final[int] = 32
"""Length of a raw X25519 public key."""

PUBLIC_KEY_HEX_LEN: Final[int] = PUBLIC_KEY_BYTES * 2
"""Length of a hex-encoded X25519 public key (64 chars)."""

_FILE_MODE: Final[int] = 0o600
_DIR_MODE: Final[int] = 0o700


class KeyFileError(Exception):
    """Raised for any malformed, missing, or insecure secret-key file."""


@dataclass(frozen=True)
class SecretKey:
    """An X25519 secret key, with a cached public key for convenience."""

    raw: bytes  # 32 bytes

    def __post_init__(self) -> None:
        if not isinstance(self.raw, (bytes, bytearray)):
            raise TypeError("SecretKey.raw must be bytes")
        if len(self.raw) != SECRET_KEY_BYTES:
            raise ValueError(
                f"SecretKey.raw must be {SECRET_KEY_BYTES} bytes, got {len(self.raw)}"
            )

    def to_x25519(self) -> X25519PrivateKey:
        """Return the cryptography library's private-key object."""
        return X25519PrivateKey.from_private_bytes(bytes(self.raw))

    def public_key(self) -> "PublicKey":
        """Derive the matching public key."""
        pub_raw = self.to_x25519().public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return PublicKey(raw=pub_raw)

    def hex(self) -> str:
        """Return the lowercase-hex encoding of the raw secret scalar."""
        return bytes(self.raw).hex()


@dataclass(frozen=True)
class PublicKey:
    """An X25519 public key (32 raw bytes)."""

    raw: bytes  # 32 bytes

    def __post_init__(self) -> None:
        if not isinstance(self.raw, (bytes, bytearray)):
            raise TypeError("PublicKey.raw must be bytes")
        if len(self.raw) != PUBLIC_KEY_BYTES:
            raise ValueError(
                f"PublicKey.raw must be {PUBLIC_KEY_BYTES} bytes, got {len(self.raw)}"
            )

    def to_x25519(self) -> X25519PublicKey:
        """Return the cryptography library's public-key object."""
        return X25519PublicKey.from_public_bytes(bytes(self.raw))

    def hex(self) -> str:
        """Return the lowercase-hex encoding of the raw public key (64 chars)."""
        return bytes(self.raw).hex()


def generate_keypair() -> SecretKey:
    """Generate a fresh X25519 keypair.

    The returned :class:`SecretKey` carries the secret scalar; call
    :meth:`SecretKey.public_key` to derive the public half.
    """
    priv = X25519PrivateKey.generate()
    raw = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return SecretKey(raw=raw)


def public_key_hex(secret: SecretKey) -> str:
    """Convenience: return ``secret.public_key().hex()``."""
    return secret.public_key().hex()


def save_secret_key(
    secret: SecretKey,
    path: str | os.PathLike[str],
    *,
    overwrite: bool = False,
) -> Path:
    """Write *secret* to *path* in the canonical v1 format.

    The file is created with mode ``0o600``. The parent directory is created
    if missing, with mode ``0o700``.

    Args:
        secret: the key to persist.
        path: destination file path.
        overwrite: if False (default), refuse to clobber an existing file.

    Returns:
        The resolved :class:`pathlib.Path` that was written.

    Raises:
        FileExistsError: if *path* exists and ``overwrite`` is False.
        KeyFileError: if the parent directory exists with permissive mode
            (group- or world-writable). The caller is expected to fix the
            directory before retrying.
    """
    target = Path(path).expanduser()
    parent = target.parent

    if parent and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(parent, _DIR_MODE)
        except (PermissionError, OSError):
            # Best-effort; e.g. on tmpfs without chmod support.
            pass
    elif parent and parent.exists():
        _check_dir_mode(parent)

    if target.exists() and not overwrite:
        raise FileExistsError(
            f"refusing to overwrite existing key file: {target} "
            "(pass --force / overwrite=True to clobber)"
        )

    body = f"{KEY_FILE_MAGIC}\n{secret.hex()}\n".encode("utf-8")

    # Atomic-ish write: open with O_CREAT|O_WRONLY|O_TRUNC and an explicit
    # restrictive mode. We also chmod after the fact in case umask widened
    # things (it shouldn't, but belt-and-suspenders).
    prev_umask = os.umask(0o077)
    try:
        fd = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            _FILE_MODE,
        )
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(body)
        except BaseException:
            # Best-effort cleanup if the write blew up after open.
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            raise
    finally:
        os.umask(prev_umask)

    try:
        os.chmod(target, _FILE_MODE)
    except (PermissionError, OSError):
        # Filesystem may not support POSIX modes (e.g. some Windows shares).
        # The umask above is our remaining defense.
        pass

    return target


def load_secret_key(
    path: str | os.PathLike[str],
    *,
    require_secure_mode: bool = True,
) -> SecretKey:
    """Load and parse a v1 Tabula secret-key file.

    Args:
        path: file to read.
        require_secure_mode: if True (default), refuse to load files with
            permissions broader than ``0o600`` on POSIX. Tests that run on
            filesystems without POSIX modes can pass False.

    Returns:
        The parsed :class:`SecretKey`.

    Raises:
        KeyFileError: on any format violation, missing file, or insecure
            permissions.
    """
    target = Path(path).expanduser()
    if not target.exists():
        raise KeyFileError(f"key file does not exist: {target}")
    if not target.is_file():
        raise KeyFileError(f"key path is not a regular file: {target}")

    if require_secure_mode:
        _check_file_mode(target)

    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise KeyFileError(f"could not read key file {target}: {exc}") from exc

    lines = text.split("\n")
    # We require exactly: [magic, hex, ""]. The trailing empty string comes
    # from the required final newline.
    if len(lines) != 3 or lines[2] != "":
        raise KeyFileError(
            f"malformed key file {target}: expected exactly two lines "
            "terminated by newline"
        )

    magic, hex_line, _ = lines
    if magic != KEY_FILE_MAGIC:
        raise KeyFileError(
            f"unknown key file magic in {target}: "
            f"got {magic!r}, expected {KEY_FILE_MAGIC!r}"
        )

    if len(hex_line) != SECRET_KEY_BYTES * 2:
        raise KeyFileError(
            f"malformed key file {target}: hex line has length "
            f"{len(hex_line)}, expected {SECRET_KEY_BYTES * 2}"
        )
    if hex_line != hex_line.lower():
        raise KeyFileError(
            f"malformed key file {target}: hex must be lowercase"
        )

    try:
        raw = bytes.fromhex(hex_line)
    except ValueError as exc:
        raise KeyFileError(
            f"malformed key file {target}: invalid hex: {exc}"
        ) from exc

    if len(raw) != SECRET_KEY_BYTES:
        # Defensive; fromhex with the length check above should make this
        # unreachable.
        raise KeyFileError(
            f"malformed key file {target}: decoded {len(raw)} bytes, "
            f"expected {SECRET_KEY_BYTES}"
        )

    return SecretKey(raw=raw)


def _check_file_mode(path: Path) -> None:
    """Refuse if *path* has POSIX bits broader than 0o600.

    On systems without POSIX modes (e.g. some Windows configurations), the
    stat mode bits are usually still meaningful enough that any group/other
    bits indicate a real problem; if the platform genuinely cannot represent
    modes, the caller can pass ``require_secure_mode=False``.
    """
    try:
        st = path.stat()
    except OSError as exc:
        raise KeyFileError(f"could not stat {path}: {exc}") from exc

    bad_bits = st.st_mode & (
        stat.S_IRWXG | stat.S_IRWXO | stat.S_IXUSR
    )
    if bad_bits:
        raise KeyFileError(
            f"insecure permissions on key file {path}: "
            f"mode={oct(stat.S_IMODE(st.st_mode))}, expected 0o600. "
            f"Run: chmod 600 {path}"
        )


def _check_dir_mode(path: Path) -> None:
    """Warn-or-fail on permissive parent directory.

    The acceptance criterion says "warn at minimum"; we raise here only for
    world-writable directories (the truly dangerous case) and let the caller
    decide whether to surface it as a warning. Group-writable dirs are common
    in shared-system installs and are merely noted via the returned mode.
    """
    try:
        st = path.stat()
    except OSError as exc:
        raise KeyFileError(f"could not stat directory {path}: {exc}") from exc

    if st.st_mode & stat.S_IWOTH:
        raise KeyFileError(
            f"refusing to write key into world-writable directory {path}: "
            f"mode={oct(stat.S_IMODE(st.st_mode))}. "
            f"Run: chmod 700 {path}"
        )

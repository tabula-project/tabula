"""Cryptography primitives for the Tabula wire layer.

Currently exposes static-key file I/O. The Noise XX wrapper lands in #18 and
will live alongside ``keys`` in this package.
"""

from tabula_wire.crypto.keys import (
    KEY_FILE_MAGIC,
    KeyFileError,
    PublicKey,
    SecretKey,
    generate_keypair,
    load_secret_key,
    public_key_hex,
    save_secret_key,
)

__all__ = [
    "KEY_FILE_MAGIC",
    "KeyFileError",
    "PublicKey",
    "SecretKey",
    "generate_keypair",
    "load_secret_key",
    "public_key_hex",
    "save_secret_key",
]

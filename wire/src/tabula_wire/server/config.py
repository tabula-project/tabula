"""Server configuration.

Values resolve from environment variables when present, falling back to
sensible defaults. The session manager (#25) reads only :class:`ServerConfig`;
listener (#20) and driver (#22) layers may extend or compose with it.

Env vars
--------

``TABULA_SERVER_CWD``
    Absolute path to the working directory the ``claude`` CLI subprocess
    will run in (one process per session, fresh cwd per session per epic
    #13). Defaults to the current process cwd.

``TABULA_MAX_CONCURRENT_SESSIONS``
    Integer cap on concurrently active sessions enforced by the listener
    via an ``asyncio.Semaphore``. Defaults to ``4``.

``TABULA_SERVER_VERSION``
    Server version string echoed back in :class:`Welcome`. Defaults to a
    package-derived constant.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


# Locked v1 wire protocol version. Bump only when breaking the wire schema
# (issue #16). Negotiated on Hello.
PROTOCOL_VERSION: int = 1

# Default server identity string. Overridable via env for ops, but the
# Welcome frame does not promise stability of this value across deploys.
DEFAULT_SERVER_VERSION: str = "tabula-server/0.1.0"

# Default cap on concurrent sessions. Tuned conservative because each
# session spawns one claude subprocess, which is heavyweight.
DEFAULT_MAX_CONCURRENT_SESSIONS: int = 4


@dataclass(frozen=True)
class ServerConfig:
    """Static, per-process server configuration."""

    cwd: Path
    max_concurrent_sessions: int
    protocol_version: int
    server_version: str

    @classmethod
    def from_env(cls) -> ServerConfig:
        """Build a config from the process environment."""
        cwd_str = os.environ.get("TABULA_SERVER_CWD", os.getcwd())
        max_sessions_str = os.environ.get(
            "TABULA_MAX_CONCURRENT_SESSIONS",
            str(DEFAULT_MAX_CONCURRENT_SESSIONS),
        )
        try:
            max_sessions = int(max_sessions_str)
        except ValueError as exc:
            raise ValueError(
                "TABULA_MAX_CONCURRENT_SESSIONS must be an integer, "
                f"got: {max_sessions_str!r}"
            ) from exc
        if max_sessions <= 0:
            raise ValueError(
                "TABULA_MAX_CONCURRENT_SESSIONS must be positive, "
                f"got: {max_sessions}"
            )

        return cls(
            cwd=Path(cwd_str).resolve(),
            max_concurrent_sessions=max_sessions,
            protocol_version=PROTOCOL_VERSION,
            server_version=os.environ.get(
                "TABULA_SERVER_VERSION", DEFAULT_SERVER_VERSION
            ),
        )


__all__ = [
    "DEFAULT_MAX_CONCURRENT_SESSIONS",
    "DEFAULT_SERVER_VERSION",
    "PROTOCOL_VERSION",
    "ServerConfig",
]

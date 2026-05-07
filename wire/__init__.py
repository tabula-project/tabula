"""Tabula wire transport: TCP + Noise XX + length-prefixed protobuf frames.

This package owns the bytes-on-the-wire layer. Sub-packages:

- ``wire.framing``  — length-prefix codec (shared between server and client)
- ``wire.crypto``   — Noise XX wrapper (real impl in #18, stub here for now)
- ``wire.proto``    — generated protobuf bindings (real impl in #16, stub here)
- ``wire.server``   — server-side TCP listener + Noise responder + frame loop

See ``wire/README.md`` for the relationship between #16, #18, and this package.
"""

__all__: list[str] = []

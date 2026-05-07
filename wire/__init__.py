"""Tabula wire transport package.

Top-level layout:
- ``wire.framing``: length-prefix codec (shared by client + server).
- ``wire.crypto``: Noise XX wrapper (stubbed here; final form lands in #18).
- ``wire.proto``: protobuf chat frame types (stubbed here; final form lands in #16).
- ``wire.client``: client-side dialer + ChatChannel (this issue, #27).
- ``wire.server``: server-side listener (separate issue, #20).
"""

__all__: list[str] = []

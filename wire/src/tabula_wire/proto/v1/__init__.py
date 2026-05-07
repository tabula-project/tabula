"""tabula.wire.v1 — stub bindings.

Issue #36 contributes the ``ErrorFrame.Code`` enum values its acceptance
criteria call out:

    PROTOCOL, CLAUDE_CRASHED, CLAUDE_TIMEOUT, AT_CAPACITY, INTERNAL

When #16 lands and generates the real ``chat_pb2.py``, this stub goes away
and consumers import the same names from the generated module. Until then,
this stub keeps `tabula_wire.errors` self-contained and testable.

TODO(#45): once #45's tooling has produced ``chat_pb2.py``, replace the
``_ErrorFrameCode`` and ``ErrorFrame`` definitions below with re-exports
from the generated module.

The integer values match the issue's prescribed client exit-code table
(PROTOCOL=2, CLAUDE_CRASHED=3, CLAUDE_TIMEOUT=4, AT_CAPACITY=5, INTERNAL=6),
with 0 reserved for ``UNSPECIFIED`` (proto3 default), and 1 reserved for
future ``OK``/``CANCELED`` semantics if needed. #16 may renumber when its
generated proto enum lands; the mapping table in
``tabula_wire.errors.exception_to_error_code`` translates exception types to
``ErrorFrame.Code`` *by name*, so a renumber there does not break #36.
"""

from __future__ import annotations

from enum import IntEnum
from dataclasses import dataclass


class _ErrorFrameCode(IntEnum):
    """Stub of ``tabula.wire.v1.ErrorFrame.Code``.

    Values track the issue's client exit-code table.
    """

    UNSPECIFIED = 0
    PROTOCOL = 2
    CLAUDE_CRASHED = 3
    CLAUDE_TIMEOUT = 4
    AT_CAPACITY = 5
    INTERNAL = 6


@dataclass(frozen=True)
class ErrorFrame:
    """Stub for ``tabula.wire.v1.ErrorFrame``.

    Real protobuf bindings replace this when #16 lands. The shape here is
    intentionally minimal — only what `tabula_wire.errors` needs to round-
    trip exception types to wire codes.
    """

    code: _ErrorFrameCode = _ErrorFrameCode.UNSPECIFIED
    message: str = ""
    turn_id: str = ""

    # Expose ``Code`` as a class attribute so ``ErrorFrame.Code.PROTOCOL``
    # works exactly like it will once protobuf-generated bindings land.
    Code = _ErrorFrameCode


__all__ = ["ErrorFrame"]

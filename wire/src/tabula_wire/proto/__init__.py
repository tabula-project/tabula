"""Protobuf bindings for tabula.wire.v1.

Stub package. Issue #36 owns the ``ErrorFrame.Code`` enum values; full
generated bindings ship with #16/#45. Until those land, ``ErrorFrame.Code``
is exposed as a plain ``IntEnum`` so other modules can import it without
depending on protoc-generated code.

TODO(#45): replace this stub with re-exports from the generated bindings
once #45's `proto/build.sh` + generated `chat_pb2.py` land. The mapping
table in `tabula_wire.errors` looks up codes by name, so the swap is
source-compatible as long as the enum names are preserved.

The stub uses the values that #36 contributes per its acceptance criteria:
``PROTOCOL``, ``CLAUDE_CRASHED``, ``CLAUDE_TIMEOUT``, ``AT_CAPACITY``,
``INTERNAL``. When #16's generated bindings replace this stub, the names
must be preserved so the mapping table in ``tabula_wire.errors`` keeps
working without source changes.
"""

from tabula_wire.proto.v1 import ErrorFrame

__all__ = ["ErrorFrame"]

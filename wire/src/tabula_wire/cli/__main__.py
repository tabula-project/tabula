"""Allow ``python -m tabula_wire.cli`` invocation."""

from __future__ import annotations

import sys

from tabula_wire.cli import main

if __name__ == "__main__":
    sys.exit(main())

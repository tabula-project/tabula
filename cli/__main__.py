"""Allow `python -m cli ...` for local development.

The installed ``tabula`` console script (see ``cli/pyproject.toml``) is the
intended user-facing entrypoint; this shim only exists so the worktree
checkout is exercisable without an editable install.
"""

from __future__ import annotations

import sys

from cli import main

if __name__ == "__main__":
    sys.exit(main())

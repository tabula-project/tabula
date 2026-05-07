"""Pytest config for `cli/tests`.

Adds the worktree root to ``sys.path`` so ``import cli`` and ``import wire``
work without an editable install. The real pyproject (#29 ships a
``[project.scripts] tabula = cli:main`` entry) is wired in the same PR but
running ``pytest`` against an uninstalled checkout is the fast loop tests
should support.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

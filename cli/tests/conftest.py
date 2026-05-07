"""Pytest config for `cli/tests`.

Adds ``cli/src`` to ``sys.path`` so ``import tabula_cli`` works without
an editable install. The canonical layout (#57) ships an editable-install
flow (`pip install -e cli`); the path-shim here only supports the
inner-loop test workflow against an uninstalled checkout.

The companion ``tabula_wire`` package is provided by sibling PRs (#45 /
#54 / #56). Those packages must be importable on ``sys.path`` for tests
to collect.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

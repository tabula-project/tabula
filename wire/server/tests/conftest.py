"""Pytest config for wire/server tests.

Ensures the parent directory is on ``sys.path`` so tests can import
``claude_driver`` regardless of whether the package has been installed.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PARENT = _HERE.parent  # wire/server/

if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

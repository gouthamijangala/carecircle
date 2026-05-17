"""Vercel ASGI entrypoint for CareCircle.

The local development entrypoint remains ``main.py``. Vercel imports ``app``
from this module and serves it as a Python serverless function.
"""

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from main import app  # noqa: E402,F401

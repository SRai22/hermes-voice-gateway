"""Read secrets/config the way Hermes wants: profile-scoped, not raw ``os.getenv``.

Falls back to ``os.environ`` only when Hermes is not importable (tests, the
local dev harness).
"""
from __future__ import annotations

import os


def secret(name: str, default: str = "") -> str:
    try:
        from gateway.platforms._shared import get_scoped_secret
    except ImportError:
        return os.environ.get(name, default)
    return get_scoped_secret(name, default)

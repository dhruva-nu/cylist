"""``cylist`` — the Cylist command line.

This package talks to the Cylist HTTP API and to nothing else. It does not
import the backend: everything it does, it does through ``/api/v1``, which is
the whole point of it existing. If a command here cannot be written, the API is
missing something.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"

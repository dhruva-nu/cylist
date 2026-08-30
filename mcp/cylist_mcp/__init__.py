"""An MCP server that puts Cylist's HTTP API in front of an agent.

Like the CLI, this package depends on the Cylist API over HTTP and not on the
backend. The tools here are a thin mapping onto ``/api/v1``; the authority they
have is exactly the authority of the token they are configured with, which is
the point of scoped tokens existing.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"

"""One module per command group.

Each exposes ``register(subparsers)``, which adds its parsers and attaches a
handler to each with ``set_defaults(handler=...)``. :mod:`cylist_cli.main`
knows nothing about any individual command beyond that convention.
"""

from __future__ import annotations

from cylist_cli.commands import (
    activity,
    auth,
    board,
    files,
    goals,
    hook,
    people,
    projects,
    tasks,
    vault,
    work,
)

REGISTRARS = (
    auth.register,
    projects.register,
    board.register,
    tasks.register,
    goals.register,
    people.register,
    files.register,
    vault.register,
    activity.register,
    work.register,
    hook.register,
)

__all__ = ["REGISTRARS"]

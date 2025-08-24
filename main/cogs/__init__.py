"""Cogs for the main application."""

from __future__ import annotations

import pkgutil
from typing import Final, Iterator, List

# Static list of cog module names for the benefit of type checkers.  At runtime
# a dynamic discovery still happens through :func:`iter_cog_names` so new files
# are picked up automatically.
COG_MODULES: Final[List[str]] = ["pari_xp", "dev_healthcheck"]


def iter_cog_names() -> Iterator[str]:
    """Yield available cog module names discovered at runtime."""

    for module in pkgutil.iter_modules(__path__):
        yield module.name


__all__: Final = ["COG_MODULES", "iter_cog_names"]

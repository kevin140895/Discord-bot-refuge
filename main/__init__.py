"""Main package for additional cogs and utilities."""

from typing import Final

# Export the ``cogs`` subpackage explicitly so mypy does not assume implicit
# re-exports from ``main``.
__all__: Final = ["cogs"]

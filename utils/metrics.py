"""Simple instrumentation helpers."""

from __future__ import annotations

import logging
import time
from collections import Counter
from contextlib import contextmanager
from typing import Iterator

logger = logging.getLogger("metrics")

# Global counter tracking errors per label
errors: Counter[str] = Counter()


@contextmanager
def measure(label: str) -> Iterator[None]:
    """Measure execution time of a block of code.

    Records the duration at DEBUG level and counts exceptions per label.
    """
    start = time.perf_counter()
    try:
        yield
    except Exception:
        errors[label] += 1
        raise
    finally:
        elapsed = time.perf_counter() - start
        logger.debug("%s took %.3fs", label, elapsed)

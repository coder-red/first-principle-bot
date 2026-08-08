"""Logging and counters.

This app previously reported itself through four `print()` calls, which meant
the questions you actually ask of a running deploy — which provider is serving
traffic, how often the repair pass fires, whether anyone is hitting the rate
limit — had no answer at all.

Deliberately not a metrics backend. Counters live in process memory and reset
on restart, same as the explore pool and the limiters; they are there to be
read off /api/health while looking at a deploy, not to be scraped over time.
"""

import logging
import os
import sys
import threading
from typing import Dict

LOG = logging.getLogger("fpb")

_CONFIGURED = False


def configure_logging() -> None:
    """Attach a handler to the `fpb` logger. Safe to call more than once.

    Under uvicorn the root logger is already configured, so `propagate` is left
    on and this only adds a handler when nothing else would print the record —
    otherwise every line appears twice.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    level = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
    LOG.setLevel(getattr(logging, level, logging.INFO))

    if not logging.getLogger().handlers and not LOG.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        ))
        LOG.addHandler(handler)


class Counters:
    """Monotonic counters, keyed by a dotted name.

    Locked because uvicorn serves from a thread pool for sync handlers, and a
    lost increment on a diagnostic counter is the kind of bug nobody ever
    tracks down.
    """

    def __init__(self):
        self._counts: Dict[str, int] = {}
        self._lock = threading.Lock()

    def bump(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counts[name] = self._counts.get(name, 0) + amount

    def snapshot(self) -> Dict[str, int]:
        with self._lock:
            return dict(sorted(self._counts.items()))

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()


COUNTERS = Counters()

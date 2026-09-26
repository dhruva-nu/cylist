"""Observers that report each call as one line: to a stream, or to stdlib logging."""
from __future__ import annotations

import logging
import sys
from typing import Optional, TextIO

from ._core import Metrics, RequestEvent, format_event, parse_metrics


class ConsoleObserver:
    """Print one line per call to stderr (or `stream`), with the chosen metrics.

        Jev(observe=ConsoleObserver("tokens"))       # same as Jev(observe="tokens")
    """

    def __init__(self, metrics: Metrics = True, stream: Optional[TextIO] = None):
        self.metrics = parse_metrics(metrics)
        self.stream = stream

    def __call__(self, event: RequestEvent) -> None:
        stream = self.stream or sys.stderr  # looked up per call so pytest's capsys and redirects work
        print(format_event(event, self.metrics), file=stream, flush=True)

    def __repr__(self):
        return "ConsoleObserver(%r)" % sorted(self.metrics)


class LogObserver:
    """Log one record per call on logger "jev" (or `logger`): successes at `level`, failures at WARNING.

    The event is attached to the record as `record.jev_event` for structured handlers.
    """

    def __init__(
        self,
        metrics: Metrics = True,
        logger: Optional[logging.Logger] = None,
        level: int = logging.INFO,
        error_level: int = logging.WARNING,
    ):
        self.metrics = parse_metrics(metrics)
        self.logger = logger or logging.getLogger("jev")
        self.level = level
        self.error_level = error_level

    def __call__(self, event: RequestEvent) -> None:
        level = self.level if event.ok else self.error_level
        if self.logger.isEnabledFor(level):
            self.logger.log(level, "%s", format_event(event, self.metrics), extra={"jev_event": event})

    def __repr__(self):
        return "LogObserver(%r, logger=%r)" % (sorted(self.metrics), self.logger.name)

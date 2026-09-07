"""Durable capture of raw metric fragments, so a session can be re-derived later.

Console mode runs a terminal UI that owns stdout, and anything printed from an event
handler is swallowed by it. The 7 Sep run lost every metric line that way and produced a
session with no recoverable timings at all, so capture writes to a file rather than to the
terminal.

JSONL rather than parsed console text. The old text logs are recovered with a regex in
``tools/replay_log.py``, which works but silently returns ``None`` for any field whose repr
changes between SDK versions. A field that quietly becomes ``None`` in a latency table is
worse than one that fails loudly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Metric types worth persisting. Everything else carries no join key and no timing.
_KINDS = ("EOUMetrics", "LLMMetrics", "TTSMetrics")


class JsonlMetricSink:
    """Append one JSON object per metric fragment, flushed on every write.

    Flushed eagerly because the interesting sessions are the ones that end badly, and a
    buffered final turn is the one you most wanted to see.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")
        self.written = 0

    def write(self, metric: Any) -> None:
        kind = type(metric).__name__
        if kind not in _KINDS:
            return
        payload = {"kind": kind}
        payload.update(json.loads(metric.model_dump_json()))
        self._fh.write(json.dumps(payload) + "\n")
        self._fh.flush()
        self.written += 1

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()

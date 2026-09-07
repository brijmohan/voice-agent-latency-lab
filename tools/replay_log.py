"""Replay a captured agent log through the correlator.

Throwaway analysis harness, not part of the agent. Parses the metric lines printed by
the M2 handler, rebuilds real metric objects, and feeds them to TurnCorrelator in the
order they appeared.

    python tools/replay_log.py data/logs/20260824-154309-M2.log [--scramble]
    python tools/replay_log.py <log> --llm-timeout 10.0   # enable retry inference
"""

import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "latency_lab"))

from correlator import TurnCorrelator
from livekit.agents.metrics import EOUMetrics, LLMMetrics, TTSMetrics

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _field(line, key, cast=float):
    m = re.search(rf"\b{key}=('([^']*)'|[-\w.+]+)", line)
    if not m:
        return None
    raw = m.group(2) if m.group(2) is not None else m.group(1)
    if cast is bool:
        return raw == "True"
    return cast(raw)


_CLASSES = {"EOUMetrics": EOUMetrics, "LLMMetrics": LLMMetrics, "TTSMetrics": TTSMetrics}


def parse_jsonl(path):
    """Return metric objects from a JsonlMetricSink capture, in write order.

    Preferred over parse(). The regex reader below returns None for any field whose repr
    changed between SDK versions, and a field that silently becomes None in a latency table
    is worse than one that fails loudly.
    """
    out = []
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        cls = _CLASSES.get(payload.pop("kind", None))
        if cls is not None:
            out.append(cls(**payload))
    return out


def parse(path):
    """Return metric objects in the order they were emitted. Legacy console-text logs."""
    out = []
    for line in ANSI.sub("", Path(path).read_text(errors="ignore")).splitlines():
        if line.startswith("EOUMetrics "):
            out.append(
                EOUMetrics(
                    timestamp=_field(line, "timestamp"),
                    end_of_utterance_delay=_field(line, "end_of_utterance_delay"),
                    transcription_delay=_field(line, "transcription_delay"),
                    on_user_turn_completed_delay=_field(line, "on_user_turn_completed_delay"),
                    speech_id=_field(line, "speech_id", str),
                )
            )
        elif line.startswith("LLMMetrics "):
            out.append(
                LLMMetrics(
                    label=_field(line, "label", str),
                    request_id=_field(line, "request_id", str) or "",
                    timestamp=_field(line, "timestamp"),
                    duration=_field(line, "duration"),
                    ttft=_field(line, "ttft"),
                    cancelled=_field(line, "cancelled", bool),
                    completion_tokens=_field(line, "completion_tokens", int),
                    prompt_tokens=_field(line, "prompt_tokens", int),
                    prompt_cached_tokens=_field(line, "prompt_cached_tokens", int),
                    total_tokens=_field(line, "total_tokens", int),
                    tokens_per_second=_field(line, "tokens_per_second"),
                    speech_id=_field(line, "speech_id", str),
                )
            )
        elif line.startswith("TTSMetrics "):
            out.append(
                TTSMetrics(
                    label=_field(line, "label", str),
                    request_id=_field(line, "request_id", str) or "",
                    timestamp=_field(line, "timestamp"),
                    ttfb=_field(line, "ttfb"),
                    duration=_field(line, "duration"),
                    audio_duration=_field(line, "audio_duration"),
                    cancelled=_field(line, "cancelled", bool),
                    characters_count=_field(line, "characters_count", int),
                    streamed=_field(line, "streamed", bool),
                    segment_id=_field(line, "segment_id", str),
                    speech_id=_field(line, "speech_id", str),
                )
            )
    return out


def _llm_timeout_arg():
    """The per-attempt APIConnectOptions.timeout the session ran with, if you know it.

    Off by default. A ttft above it proves at least one attempt timed out, but only if the
    value actually matches what the deployment configured, so it is never assumed.
    """
    if "--llm-timeout" not in sys.argv:
        return None
    return float(sys.argv[sys.argv.index("--llm-timeout") + 1])


def main():
    path = sys.argv[1]
    metrics = parse_jsonl(path) if path.endswith(".jsonl") else parse(path)

    if "--scramble" in sys.argv:
        # Correctness must not depend on arrival order. Shuffle within a window so
        # events still roughly interleave the way a real session would.
        random.seed(0)
        random.shuffle(metrics)

    c = TurnCorrelator(llm_timeout=_llm_timeout_arg())
    for m in metrics:
        c.on_metric(m)

    print(f"parsed {len(metrics)} metric objects from {Path(path).name}")
    print(f"emitted {len(c.turns)} completed turns\n")
    print(f"{'#':>2} {'eou':>6} {'tdel':>6} {'endpt':>6} {'ttft':>7} {'ttfb':>6} {'TTFA':>7}")
    print("-" * 46)
    for i, t in enumerate(c.turns, 1):
        print(
            f"{i:>2} {t.end_of_utterance_delay:6.3f} {t.transcription_delay:6.3f} "
            f"{t.endpointing_delay:6.3f} {t.llm_ttft:7.3f} {t.tts_ttfb:6.3f} {t.ttfa:7.3f}"
        )
    print()
    c.flush_incomplete()


if __name__ == "__main__":
    main()

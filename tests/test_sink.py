"""Unit tests for the raw metric sink.

The sink exists because console mode swallowed stdout and a whole session's timings were
lost. Its one job is that what went in can be read back out.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from livekit.agents.metrics import EOUMetrics, LLMMetrics, STTMetrics

from apresvous.sink import JsonlMetricSink


def eou(speech_id="s1", delay=0.578):
    return EOUMetrics(timestamp=0.0, end_of_utterance_delay=delay, transcription_delay=0.361,
                      on_user_turn_completed_delay=0.0, speech_id=speech_id)


def test_written_metrics_survive_a_round_trip(tmp_path):
    p = tmp_path / "m.jsonl"
    s = JsonlMetricSink(p)
    s.write(eou(delay=0.581))
    s.close()
    rec = json.loads(p.read_text().strip())
    assert rec["kind"] == "EOUMetrics"
    assert rec["end_of_utterance_delay"] == 0.581
    assert rec["speech_id"] == "s1"


def test_unmodelled_metric_types_are_ignored(tmp_path):
    """STT carries no speech_id and no stage timing, so persisting it would only add noise."""
    p = tmp_path / "m.jsonl"
    s = JsonlMetricSink(p)
    s.write(STTMetrics(label="t", request_id="", timestamp=0.0, duration=0.0,
                       audio_duration=0.0, streamed=True))
    s.close()
    assert s.written == 0
    assert p.read_text() == ""


def test_each_write_is_flushed(tmp_path):
    """The interesting sessions are the ones that end badly, and a buffered final turn is
    the one you most wanted to see."""
    p = tmp_path / "m.jsonl"
    s = JsonlMetricSink(p)
    s.write(eou())
    assert p.read_text().count("\n") == 1, "not flushed before close"
    s.close()


def test_appends_rather_than_truncating(tmp_path):
    p = tmp_path / "m.jsonl"
    for _ in range(2):
        s = JsonlMetricSink(p)
        s.write(eou())
        s.close()
    assert len(p.read_text().strip().splitlines()) == 2


def test_creates_missing_parent_directories(tmp_path):
    p = tmp_path / "deep" / "nested" / "m.jsonl"
    s = JsonlMetricSink(p)
    s.write(LLMMetrics(label="l", request_id="r", timestamp=0.0, duration=1.0, ttft=0.5,
                       cancelled=False, completion_tokens=1, prompt_tokens=1,
                       prompt_cached_tokens=0, total_tokens=2, tokens_per_second=1.0,
                       speech_id="s1"))
    s.close()
    assert p.exists() and s.written == 1

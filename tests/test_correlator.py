"""Acceptance tests for the happy-path correlator.

The interesting assertion is not that it adds three numbers. It is that the result does
not depend on the order the fragments arrive in.
"""

import itertools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "latency_lab"))

from correlator import TurnCorrelator
from livekit.agents.metrics import EOUMetrics, LLMMetrics, TTSMetrics


def eou(speech_id, delay=0.578, transcription=0.361):
    return EOUMetrics(
        timestamp=0.0,
        end_of_utterance_delay=delay,
        transcription_delay=transcription,
        on_user_turn_completed_delay=0.0,
        speech_id=speech_id,
    )


def llm(speech_id, ttft=0.534):
    return LLMMetrics(
        label="test", request_id="r", timestamp=0.0, duration=1.0, ttft=ttft,
        cancelled=False, completion_tokens=1, prompt_tokens=1, prompt_cached_tokens=0,
        total_tokens=2, tokens_per_second=1.0, speech_id=speech_id,
    )


def tts(speech_id, ttfb=0.135, segment_id="seg"):
    return TTSMetrics(
        label="test", request_id="r", timestamp=0.0, ttfb=ttfb, duration=1.0,
        audio_duration=1.0, cancelled=False, characters_count=10, streamed=True,
        segment_id=segment_id, speech_id=speech_id,
    )


def test_single_turn_arithmetic():
    c = TurnCorrelator()
    for m in (eou("s1"), llm("s1"), tts("s1")):
        c.on_metric(m)

    assert len(c.turns) == 1
    t = c.turns[0]
    assert t.endpointing_delay == pytest.approx(0.217)
    assert t.ttfa == pytest.approx(1.247)


@pytest.mark.parametrize("order", list(itertools.permutations([0, 1, 2])))
def test_every_arrival_order_gives_the_same_record(order):
    """All 6 orderings. TTS before LLM before EOU must work."""
    fragments = [eou("s1"), llm("s1"), tts("s1")]
    c = TurnCorrelator()
    for i in order:
        c.on_metric(fragments[i])

    assert len(c.turns) == 1
    assert c.turns[0].ttfa == pytest.approx(1.247)


def test_interleaved_turns_do_not_bleed():
    c = TurnCorrelator()
    for m in (
        eou("s1", 0.578, 0.361), eou("s2", 2.501, 0.349),
        llm("s2", 0.505), llm("s1", 0.534),
        tts("s2", 0.140), tts("s1", 0.135),
    ):
        c.on_metric(m)

    got = {t.speech_id: round(t.ttfa, 3) for t in c.turns}
    assert got == {"s1": 1.247, "s2": 3.146}


def test_first_tts_segment_wins():
    """Later segments stream behind the first, so counting them overstates the wait."""
    c = TurnCorrelator()
    for m in (eou("s1"), llm("s1"), tts("s1", 0.135, "seg1"), tts("s1", 9.0, "seg2")):
        c.on_metric(m)

    assert len(c.turns) == 1
    assert c.turns[0].tts_ttfb == pytest.approx(0.135)


def test_metrics_without_speech_id_are_ignored():
    """STTMetrics has no speech_id: STT runs continuously and is not scoped to a turn."""
    from livekit.agents.metrics import STTMetrics

    c = TurnCorrelator()
    c.on_metric(
        STTMetrics(
            label="test", request_id="", timestamp=0.0, duration=0.0,
            audio_duration=0.0, streamed=True,
        )
    )
    assert c.turns == []


def test_incomplete_turns_are_never_emitted():
    """A turn missing TTS is not a fast turn. It must not reach the distribution."""
    c = TurnCorrelator()
    c.on_metric(eou("s1"))
    c.on_metric(llm("s1"))

    assert c.turns == []

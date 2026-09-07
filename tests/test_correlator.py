"""Acceptance tests for the happy-path correlator.

The interesting assertion is not that it adds three numbers. It is that the result does
not depend on the order the fragments arrive in.
"""

import itertools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "latency_lab"))

from correlator import TurnCorrelator, TurnOutcome
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
        label="test",
        request_id="r",
        timestamp=0.0,
        duration=1.0,
        ttft=ttft,
        cancelled=False,
        completion_tokens=1,
        prompt_tokens=1,
        prompt_cached_tokens=0,
        total_tokens=2,
        tokens_per_second=1.0,
        speech_id=speech_id,
    )


def tts(speech_id, ttfb=0.135, segment_id="seg"):
    return TTSMetrics(
        label="test",
        request_id="r",
        timestamp=0.0,
        ttfb=ttfb,
        duration=1.0,
        audio_duration=1.0,
        cancelled=False,
        characters_count=10,
        streamed=True,
        segment_id=segment_id,
        speech_id=speech_id,
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
        eou("s1", 0.578, 0.361),
        eou("s2", 2.501, 0.349),
        llm("s2", 0.505),
        llm("s1", 0.534),
        tts("s2", 0.140),
        tts("s1", 0.135),
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
            label="test",
            request_id="",
            timestamp=0.0,
            duration=0.0,
            audio_duration=0.0,
            streamed=True,
        )
    )
    assert c.turns == []


def test_incomplete_turns_are_never_emitted():
    """A turn missing TTS is not a fast turn. It must not reach the distribution."""
    c = TurnCorrelator()
    c.on_metric(eou("s1"))
    c.on_metric(llm("s1"))

    assert c.turns == []


# ---------------------------------------------------------------------------
# Outcome classification.
#
# The happy path above only proves the arithmetic and the join. These prove the
# thing that decides whether the published table is honest: that a speech_id
# which is not a turn is counted rather than silently dropped.
# ---------------------------------------------------------------------------


class _UnmodelledMetric:
    """A metric type the correlator does not know about, but which carries a speech_id."""

    def __init__(self, speech_id):
        self.speech_id = speech_id


def test_unmodelled_metric_does_not_create_a_phantom_turn():
    """An unknown type must be dropped before any state exists for its speech_id.

    Creating the pending entry first and type-checking second would report a speech_id
    that never had a single usable field as an incomplete turn.
    """
    c = TurnCorrelator()
    c.on_metric(_UnmodelledMetric("ghost"))

    assert c.turns == []
    assert c.finalize().records == []


def test_silent_turn_is_classified_not_dropped():
    """EOU + LLM and no audio. The caller spoke and heard nothing.

    It must stay out of the latency distribution, because it has no time-to-audio, and it
    must appear in the ledger, because it is the worst thing that happened in the session.
    """
    c = TurnCorrelator()
    c.on_metric(eou("s1"))
    c.on_metric(llm("s1"))

    assert c.turns == []
    summary = c.finalize()
    assert summary.counts[TurnOutcome.SILENT] == 1
    assert summary.records[0].ttfa is None


def test_agent_initiated_turn_has_no_ttfa():
    """The greeting: say/generate_reply called by our code, so no preceding end of utterance.

    user_initiated is True here. Reading that flag the other way round would classify every
    ordinary reply in the session as the greeting.
    """
    c = TurnCorrelator()
    c.on_speech_created(speech_id="greeting", user_initiated=True, source="generate_reply", handle=None)
    c.on_metric(llm("greeting"))
    c.on_metric(tts("greeting"))

    assert c.turns == []
    summary = c.finalize()
    assert summary.counts[TurnOutcome.AGENT_INITIATED] == 1


def test_cascaded_pipeline_reports_user_initiated_true_for_every_speech():
    """The shape a real livekit-agents 1.8.0 cascaded session actually produces.

    ``AgentActivity._generate_reply`` hardcodes ``user_initiated=True`` and both pipeline
    paths call it, so ordinary replies arrive carrying the same flag as the greeting.
    Classifying on that flag marks all of them agent-initiated and empties the latency
    table, which is exactly what the 7 Sep run produced: 16 speech_ids, 16 agent-initiated,
    zero turns.
    """
    c = TurnCorrelator()

    # the greeting: audio, no preceding end of utterance
    c.on_speech_created(speech_id="greeting", user_initiated=True, source="generate_reply", handle=None)
    c.on_metric(llm("greeting"))
    c.on_metric(tts("greeting"))

    # ordinary replies, reporting the very same flag
    for i in range(3):
        sid = f"turn{i}"
        c.on_speech_created(speech_id=sid, user_initiated=True, source="generate_reply", handle=None)
        for m in (eou(sid), llm(sid), tts(sid)):
            c.on_metric(m)

    assert len(c.turns) == 3, "user_initiated must not suppress ordinary replies"
    counts = c.finalize().counts
    assert counts[TurnOutcome.RESPONDED] == 3
    assert counts[TurnOutcome.AGENT_INITIATED] == 1


def test_say_is_agent_initiated_even_with_an_end_of_utterance():
    """``source == "say"`` is the one unambiguous signal, so it wins over the shape."""
    c = TurnCorrelator()
    c.on_speech_created(speech_id="s1", user_initiated=True, source="say", handle=None)
    for m in (eou("s1"), llm("s1"), tts("s1")):
        c.on_metric(m)

    assert c.turns == []
    assert c.finalize().counts[TurnOutcome.AGENT_INITIATED] == 1


def test_speculative_generation_is_counted_separately():
    """LLM only, no EOU, no audio: a preemptive generation the caller talked over."""
    c = TurnCorrelator()
    c.on_metric(llm("spec1"))

    assert c.turns == []
    assert c.finalize().counts[TurnOutcome.SPECULATIVE] == 1


def test_tool_steps_count_but_only_the_first_ttft_is_on_the_path():
    """A second LLM call happens after a tool returns, long after first audio."""
    c = TurnCorrelator()
    for m in (eou("s1"), llm("s1", 0.534), tts("s1"), llm("s1", 4.0)):
        c.on_metric(m)

    assert len(c.turns) == 1
    assert c.turns[0].llm_ttft == pytest.approx(0.534)
    assert c.finalize().records[0].llm_calls == 2


def test_late_tts_segment_is_counted_after_emission():
    """Arrives once the turn has already been delivered; must not change the recorded ttfb."""
    c = TurnCorrelator()
    for m in (eou("s1"), llm("s1"), tts("s1", 0.135, "seg1")):
        c.on_metric(m)
    c.on_metric(tts("s1", 9.0, "seg2"))

    assert c.turns[0].tts_ttfb == pytest.approx(0.135)
    assert c.finalize().records[0].tts_segments == 2


def test_retry_is_inferred_only_when_the_timeout_is_supplied():
    """ttft above the per-attempt timeout cannot come from one attempt.

    Without the configured timeout the inference is not made at all. Assuming the SDK
    default when the deployment set something else would be a fabricated finding.
    """
    slow = 16.350

    unaware = TurnCorrelator()
    for m in (eou("s1"), llm("s1", slow), tts("s1")):
        unaware.on_metric(m)
    assert unaware.turns[0].retried is False

    aware = TurnCorrelator(llm_timeout=10.0)
    for m in (eou("s1"), llm("s1", slow), tts("s1")):
        aware.on_metric(m)
    assert aware.turns[0].retried is True
    assert "10.0" in aware.turns[0].retry_evidence


def test_multiple_provider_request_ids_are_direct_retry_evidence():
    """Stream C evidence beats the timeout inference: it is observed, not derived."""

    class _Handle:
        def __init__(self):
            self.chat_items = [type("Item", (), {"id": "item_1"})()]

    c = TurnCorrelator()
    c.on_speech_created(speech_id="s1", user_initiated=False, source="generate_reply", handle=_Handle())
    for m in (eou("s1"), llm("s1"), tts("s1")):
        c.on_metric(m)
    c.on_chat_message(
        message_id="item_1",
        role="assistant",
        metrics={"provider_request_ids": ["req_a", "req_b"]},
        interrupted=False,
    )

    record = c.finalize().records[0]
    assert record.provider_request_ids == ["req_a", "req_b"]
    assert "2 provider request ids" in record.retry_evidence


def test_user_chat_messages_are_ignored():
    """provider_request_ids is assistant-only; the user message carries different fields."""
    c = TurnCorrelator()
    c.on_chat_message(
        message_id="item_user",
        role="user",
        metrics={"transcription_delay": 0.361},
        interrupted=False,
    )
    assert c._chat_metrics == {}


def test_finalize_is_stable_across_calls():
    c = TurnCorrelator()
    for m in (eou("s1"), llm("s1"), tts("s1")):
        c.on_metric(m)

    first, second = c.finalize(), c.finalize()
    assert first.counts == second.counts
    assert len(c.turns) == 1


def test_reproduces_the_24_aug_session_shape():
    """The real distribution behind the published table: 21 speech_ids, 10 usable turns.

    This is the regression test for the whole point of the module. If a future change makes
    the table look better than this, it is because something stopped being counted.
    """
    c = TurnCorrelator(llm_timeout=10.0)

    c.on_speech_created(speech_id="greeting", user_initiated=True, source="generate_reply", handle=None)
    c.on_metric(llm("greeting"))
    c.on_metric(tts("greeting"))

    for i in range(10):
        sid = f"turn{i}"
        for m in (eou(sid), llm(sid), tts(sid)):
            c.on_metric(m)

    for i in range(9):
        c.on_metric(llm(f"spec{i}"))

    c.on_metric(eou("silent"))
    c.on_metric(llm("silent"))

    summary = c.finalize()
    assert len(summary.records) == 21
    assert summary.counts == {
        TurnOutcome.RESPONDED: 10,
        TurnOutcome.SPECULATIVE: 9,
        TurnOutcome.AGENT_INITIATED: 1,
        TurnOutcome.SILENT: 1,
        TurnOutcome.INCOMPLETE: 0,
    }
    assert len(c.turns) == 10
    # 21 LLM calls for 10 turns the caller actually heard. preemptive_generation is on by
    # default and this is what it costs.
    assert summary.llm_calls_per_responded_turn == pytest.approx(2.1)

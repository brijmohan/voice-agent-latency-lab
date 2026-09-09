"""Acceptance tests for the wire correlator. Written before the implementation.

Every expected value below was computed from the real capture it names, not recalled.
Fixtures are actual sessions, so a test failing means the correlator disagrees with what
the server did, not with a hand-made scenario.

Run:  uv run pytest tests/test_wire_correlator.py -v
"""

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "latency_lab"))

FIXTURES = ROOT / "tests" / "fixtures" / "wire"
"""Five real sessions, with base64 audio payloads blanked. Every field the correlator reads
is untouched, and the trimmed files produce byte-identical results at 88 KB instead of 947."""

_corpus_env = os.environ.get("WIRE_CAPTURES", "").strip()
CORPUS = Path(_corpus_env) if _corpus_env else None
"""The full 23 x 5 sweep, which is 19 MB and not in git. Set WIRE_CAPTURES to run the
whole-corpus tests; they skip otherwise. Reproduce it with docs/recording-script.md.

Not `Path(os.environ.get(..., ""))`: `Path("")` is `PosixPath('.')`, which is a real
directory, so the skip guard would never fire and the test would glob the working directory
instead."""

from wire_correlator import TurnOutcome, correlate


def load(name, base=None):
    """Events from a capture, dropping the _capture.meta header line."""
    with ((base or FIXTURES) / f"{name}.jsonl").open() as fh:
        return [json.loads(line) for line in fh][1:]


def one_turn(name):
    turns = correlate(load(name))
    assert len(turns) == 1, f"{name}: expected exactly one turn, got {len(turns)}"
    return turns[0]


# --- the easy case must not change -----------------------------------------

def test_single_segment_turn_is_unaffected_by_the_grouping():
    """A03 has one speech segment, so last-stop and first-stop must agree.

    If this fails, the grouping is broken for the simplest possible input and nothing
    below is worth reading.
    """
    t = one_turn("A03-r1")
    assert t.segments == 1
    assert t.revisions == 1
    assert t.ttfa == pytest.approx(1.354263, abs=1e-4)
    assert t.final_transcript == "The first name is Edward Hawkins."


def test_stages_sum_to_ttfa():
    """hold + llm + tts is TTFA by construction. It should also be true in the code."""
    t = one_turn("A03-r1")
    assert t.hold + t.llm + t.continuation + t.tts == pytest.approx(t.ttfa, abs=1e-6)


# --- the regression this whole brief exists for -----------------------------

def test_multi_segment_turn_anchors_on_the_last_speech_stop():
    """E03-r1: 11 speech segments folded into 4 transcript revisions, one response.

    Anchoring on the first speech_stopped reports 13.015s. The caller waited 3.334s.
    This single test is the difference between a plausible table and a correct one.
    """
    t = one_turn("E03-r1")
    assert t.segments == 11
    assert t.revisions == 4
    assert t.ttfa == pytest.approx(3.333992, abs=1e-4)
    assert t.ttfa < 4.0, "anchored on the wrong speech_stopped"


def test_deliberate_pauses_are_one_turn_not_three():
    """C02-r1: two deliberate mid-sentence pauses, three revisions, one reply.

    The caller experienced one turn. Reporting three would invent two turns that were
    never answered and quadruple the tail.
    """
    t = one_turn("C02-r1")
    assert t.segments == 3
    assert t.revisions == 3
    assert t.ttfa == pytest.approx(2.266170, abs=1e-4)
    assert t.final_transcript.endswith("fourteenth of June.")


def test_revision_count_is_retained():
    """Revisions cost LLM work on transcripts that were superseded.

    You cannot report that cost if the correlator throws the revisions away.
    """
    assert one_turn("E03-r1").revisions == 4
    assert one_turn("F01-r1").revisions == 1


# --- the endpointer hold, which TTFA alone cannot show ----------------------

def test_incomplete_utterance_carries_the_endpointer_hold():
    """B01 is deliberately unfinished. Smart Turn scored it incomplete and added 600ms.

    That cost lands in `hold`, not in the LLM or TTS stages. A01-style clean speech holds
    for about 0.1s; this holds for about 0.68s.
    """
    b = one_turn("B01-r1")
    a = one_turn("A03-r1")
    assert b.hold == pytest.approx(0.681003, abs=1e-4)
    assert b.hold > a.hold + 0.4, "the endpointing hold is not landing in `hold`"


def test_short_utterance_has_almost_no_hold():
    t = one_turn("F01-r1")
    assert t.hold == pytest.approx(0.044088, abs=1e-4)
    assert t.final_transcript == "Yes."


# --- outcomes: every turn counted, none dropped -----------------------------

@pytest.mark.skipif(CORPUS is None or not CORPUS.is_dir(),
                    reason="set WIRE_CAPTURES to the 23x5 sweep")
def test_every_capture_in_the_corpus_yields_exactly_one_responded_turn():
    """115 captures, each one utterance. None may be silently dropped.

    The first correlator's worst bug was emitting only the turns that fit the table.
    """
    files = sorted(CORPUS.glob("*.jsonl"))
    assert len(files) == 115, "corpus is not the 23 x 5 sweep"
    for f in files:
        turns = correlate(load(f.stem, CORPUS))
        assert len(turns) == 1, f"{f.stem}: {len(turns)} turns"
        assert turns[0].outcome is TurnOutcome.RESPONDED, f"{f.stem}: {turns[0].outcome}"
        assert turns[0].ttfa is not None


def test_a_turn_with_no_response_is_representable():
    """HELD does not occur in these 115 captures and will occur.

    Synthetic, because the corpus cannot produce it: user speech, capture timed out, no
    response. It must classify as HELD with ttfa None rather than vanish.
    """
    events = [
        {"t": 0.5, "type": "input_audio_buffer.speech_started", "event": {"item_id": "i1"}},
        {"t": 2.0, "type": "input_audio_buffer.speech_stopped", "event": {"item_id": "i1"}},
        {"t": 2.1, "type": "conversation.item.input_audio_transcription.completed",
         "event": {"item_id": "i1", "transcript": "and then"}},
        {"t": 17.0, "type": "_capture.timeout", "event": {"waited_s": 15.0}},
    ]
    turns = correlate(events)
    assert len(turns) == 1
    assert turns[0].outcome is TurnOutcome.HELD
    assert turns[0].ttfa is None


def test_a_response_with_no_audio_is_silent_not_dropped():
    """SILENT does not occur in these 115 captures either. Same argument."""
    events = [
        {"t": 0.5, "type": "input_audio_buffer.speech_started", "event": {"item_id": "i1"}},
        {"t": 2.0, "type": "input_audio_buffer.speech_stopped", "event": {"item_id": "i1"}},
        {"t": 2.1, "type": "conversation.item.input_audio_transcription.completed",
         "event": {"item_id": "i1", "transcript": "hello"}},
        {"t": 3.5, "type": "response.created", "event": {"response": {"id": "resp_1"}}},
        {"t": 3.6, "type": "response.done", "event": {"response": {"id": "resp_1"}}},
    ]
    turns = correlate(events)
    assert len(turns) == 1
    assert turns[0].outcome is TurnOutcome.SILENT
    assert turns[0].ttfa is None


# --- tool turns: several responses, one turn --------------------------------

def test_a_tool_turn_is_one_turn_not_two():
    """One user turn, two responses, because a tool ran in between.

    The caller spoke once and waited once, so this is one turn however many generations it
    took. The interval between the first response and the one that spoke is `continuation`:
    tool execution plus any further generation, which the wire cannot separate.
    """
    events = [
        {"t": 0.5, "type": "input_audio_buffer.speech_started", "event": {"item_id": "i1"}},
        {"t": 2.0, "type": "input_audio_buffer.speech_stopped", "event": {"item_id": "i1"}},
        {"t": 2.1, "type": "conversation.item.input_audio_transcription.completed",
         "event": {"item_id": "i1", "transcript": "where is my order"}},
        {"t": 3.0, "type": "response.created", "event": {"response": {"id": "resp_1"}}},
        {"t": 3.1, "type": "response.function_call_arguments.done", "event": {}},
        {"t": 3.2, "type": "response.done", "event": {"response": {"id": "resp_1"}}},
        {"t": 5.2, "type": "response.created", "event": {"response": {"id": "resp_2"}}},
        {"t": 5.4, "type": "response.output_audio.delta", "event": {"delta": "AAA"}},
        {"t": 6.0, "type": "response.done", "event": {"response": {"id": "resp_2"}}},
    ]
    turns = correlate(events)
    assert len(turns) == 1
    t = turns[0]
    assert t.responses == 2
    assert t.outcome is TurnOutcome.RESPONDED
    assert t.ttfa == pytest.approx(3.4)          # 5.4 - 2.0, the whole wait
    assert t.hold == pytest.approx(0.1)
    assert t.llm == pytest.approx(0.9)           # 3.0 - 2.1, first generation
    assert t.continuation == pytest.approx(2.2)  # 5.2 - 3.0, tool plus regeneration
    assert t.tts == pytest.approx(0.2)           # 5.4 - 5.2
    assert t.hold + t.llm + t.continuation + t.tts == pytest.approx(t.ttfa)


def test_speaking_before_a_tool_leaves_continuation_at_zero():
    """The agent may answer first and call the tool afterwards.

    TTFA then ends at that first audio, and the tool time falls after it, where it belongs:
    the caller was already being spoken to.
    """
    events = [
        {"t": 2.0, "type": "input_audio_buffer.speech_stopped", "event": {"item_id": "i1"}},
        {"t": 2.1, "type": "conversation.item.input_audio_transcription.completed",
         "event": {"item_id": "i1", "transcript": "check my order"}},
        {"t": 3.0, "type": "response.created", "event": {"response": {"id": "resp_1"}}},
        {"t": 3.2, "type": "response.output_audio.delta", "event": {"delta": "one moment"}},
        {"t": 3.4, "type": "response.done", "event": {"response": {"id": "resp_1"}}},
        {"t": 6.0, "type": "response.created", "event": {"response": {"id": "resp_2"}}},
        {"t": 6.2, "type": "response.output_audio.delta", "event": {"delta": "it shipped"}},
        {"t": 6.8, "type": "response.done", "event": {"response": {"id": "resp_2"}}},
    ]
    turns = correlate(events)
    assert len(turns) == 1
    t = turns[0]
    assert t.responses == 2
    assert t.continuation == pytest.approx(0.0)
    assert t.ttfa == pytest.approx(1.2)   # 3.2 - 2.0, not 6.2


def test_new_caller_speech_still_starts_a_new_turn():
    """The merge must not swallow genuinely separate turns.

    Speech between two responses is what proves the first turn ended.
    """
    events = [
        {"t": 1.0, "type": "input_audio_buffer.speech_stopped", "event": {"item_id": "i1"}},
        {"t": 1.1, "type": "conversation.item.input_audio_transcription.completed",
         "event": {"item_id": "i1", "transcript": "hello"}},
        {"t": 2.0, "type": "response.created", "event": {"response": {"id": "resp_1"}}},
        {"t": 2.2, "type": "response.output_audio.delta", "event": {"delta": "A"}},
        {"t": 2.5, "type": "response.done", "event": {"response": {"id": "resp_1"}}},
        {"t": 4.0, "type": "input_audio_buffer.speech_stopped", "event": {"item_id": "i2"}},
        {"t": 4.1, "type": "conversation.item.input_audio_transcription.completed",
         "event": {"item_id": "i2", "transcript": "book it"}},
        {"t": 5.0, "type": "response.created", "event": {"response": {"id": "resp_2"}}},
        {"t": 5.3, "type": "response.output_audio.delta", "event": {"delta": "B"}},
        {"t": 5.6, "type": "response.done", "event": {"response": {"id": "resp_2"}}},
    ]
    turns = correlate(events)
    assert len(turns) == 2
    assert [t.responses for t in turns] == [1, 1]
    assert turns[0].ttfa == pytest.approx(1.2)
    assert turns[1].ttfa == pytest.approx(1.3)


def test_a_tool_turn_that_never_speaks_is_silent():
    events = [
        {"t": 2.0, "type": "input_audio_buffer.speech_stopped", "event": {"item_id": "i1"}},
        {"t": 2.1, "type": "conversation.item.input_audio_transcription.completed",
         "event": {"item_id": "i1", "transcript": "check my order"}},
        {"t": 3.0, "type": "response.created", "event": {"response": {"id": "resp_1"}}},
        {"t": 3.2, "type": "response.done", "event": {"response": {"id": "resp_1"}}},
        {"t": 5.0, "type": "response.created", "event": {"response": {"id": "resp_2"}}},
        {"t": 5.2, "type": "response.done", "event": {"response": {"id": "resp_2"}}},
    ]
    turns = correlate(events)
    assert len(turns) == 1
    assert turns[0].responses == 2
    assert turns[0].outcome is TurnOutcome.SILENT
    assert turns[0].ttfa is None

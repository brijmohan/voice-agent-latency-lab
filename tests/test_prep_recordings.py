"""Unit tests for the recording preparation step.

This is the layer where a silent bug does the most damage. `prep_recordings` decides where
speech starts and ends, and every downstream latency number is measured from a boundary it
chose. Trimming 50ms too much shifts every TTFA in the corpus by 50ms and nothing else in
the pipeline would notice.
"""

import math
import sys
import wave
from array import array
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from prep_recordings import RATE, THRESH_DBFS, speech_bounds


def tone(seconds, dbfs=-6.0, freq=220.0):
    amp = 32767 * (10 ** (dbfs / 20.0))
    n = int(RATE * seconds)
    return array("h", (int(amp * math.sin(2 * math.pi * freq * i / RATE)) for i in range(n)))


def silence(seconds):
    return array("h", [0] * int(RATE * seconds))


def test_finds_speech_between_silence():
    s = silence(1.0) + tone(0.5) + silence(1.5)
    first, last = speech_bounds(s)
    assert first == pytest.approx(RATE * 1.0, abs=RATE * 0.01)
    assert last == pytest.approx(RATE * 1.5, abs=RATE * 0.01)


def test_all_silence_returns_none():
    """A dead take must be reported, not trimmed into an empty file."""
    first, last = speech_bounds(silence(2.0))
    assert first is None and last is None


def test_audio_just_above_threshold_is_kept():
    """A soft final consonant sits close to the floor. Cutting it changes where speech ends.

    The threshold is -45 dBFS, so a -40 dBFS segment must survive.
    """
    s = silence(0.5) + tone(0.3, dbfs=THRESH_DBFS + 5) + silence(0.5)
    first, last = speech_bounds(s)
    assert first is not None
    assert (last - first) / RATE == pytest.approx(0.3, abs=0.02)


def test_audio_below_threshold_is_treated_as_silence():
    """Room tone and mic hiss must not be mistaken for speech, or every take starts at 0."""
    s = silence(0.5) + tone(0.3, dbfs=THRESH_DBFS - 10) + silence(0.5)
    assert speech_bounds(s) == (None, None)


def test_bounds_are_inclusive_of_the_quietest_kept_sample():
    """`last` is an index into the buffer, so it must be strictly inside it."""
    s = silence(0.2) + tone(0.4) + silence(0.2)
    first, last = speech_bounds(s)
    assert 0 <= first < last < len(s)


def test_round_trip_through_a_wav_preserves_bounds(tmp_path):
    """Guards the wav writer, since a channel or width mistake silently halves the rate."""
    s = silence(0.5) + tone(0.4) + silence(0.6)
    p = tmp_path / "t.wav"
    with wave.open(str(p), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(s.tobytes())
    with wave.open(str(p)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, RATE)
        back = memoryview(w.readframes(w.getnframes())).cast("h")
    assert speech_bounds(back) == speech_bounds(s)

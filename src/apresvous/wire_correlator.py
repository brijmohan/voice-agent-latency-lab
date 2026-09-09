"""Turn correlation for an OpenAI Realtime wire capture.

The LiveKit correlator joins metric fragments on a shared `speech_id`. Nothing like that
exists here. `response.created` carries only its own `resp_…` and **no `item_id`**, so the
protocol never says which input caused which response. There is no key, and grouping has to
be temporal.

That is the whole design constraint, and everything below follows from it.

Grouping
--------

A **turn** is every input-side event between the previous `response.done` and the next
`response.created`, together with that response's own events. One reply, one turn. A caller
who says "I'd like to book … a ceremony slot … for the fourteenth" with pauses experienced
one turn and got one answer, even though the server emitted three separate items for it.

Anchoring on the wrong end of that group is not a small error. In this corpus, taking the
*first* `speech_stopped` instead of the last reported 13.015s for E03 where the caller
waited 3.334s, and inflated the pooled P99 from 3.192s to 12.947s while producing a table
that looked entirely reasonable.

Why the input arrives as revisions
----------------------------------

An utterance with pauses does not arrive as one item. Each speech segment produces a new
`item_id` whose transcript **restates the whole utterance so far**::

    item…e94c68   "I'd like to book"
    item…bbd29b   "I'd like to book a ceremony slot."
    item…ebca56   "I'd like to book a ceremony slot for the fourteenth"

61% of turns in the reference corpus have more than one speech segment, so this is the
ordinary case. Segments and revisions are not the same count: E03 produced 11 segments
folded into 4 revisions. Both are kept, because the revisions cost real LLM work on
transcripts that were then superseded, and a correlator that discards them cannot report it.

Grouping by transcript containment was rejected. The ASR revises its own punctuation
("That's E. D. W." then "That's E D W A R D."), so prefix matching fails and you end up with
latency numbers that depend on a fuzzy string comparison.

Two stages that are not what their obvious names would say
----------------------------------------------------------

* **`hold` is not transcription time.** It is the gap from the last `speech_stopped` to the
  last completed transcript, and it contains the endpointer's deliberate delay: Smart Turn
  adds 600ms when it judges an utterance incomplete. Measured over the corpus, clean speech
  holds ~0.08s and a deliberately unfinished sentence holds ~0.69s. Naming this
  `transcription_delay` would file endpointing cost under a transcription label.

* **`llm` is total generation, not time to first token.** `response.created` is emitted when
  the LLM *finishes*, verified against the server's own MLX lock log. The wire cannot give
  TTFT on this stack at any anchor, and reporting it as TTFT would be a fabrication.

Tool turns produce several responses for one turn
-------------------------------------------------

A turn that calls a tool emits more than one ``response.created``: one generation decides to
call the tool, the tool runs, and a later generation speaks the answer. The maintainer of the
subject project put it as "tools can call other tools can continue, the assistant can speak,
call a tool, use the tool to continue speaking."

Consecutive responses with **no caller speech between them** are therefore one turn. The
caller spoke once and waited once, so they experienced one turn regardless of how many
generations it took.

This needs a stage the single-response model has no name for. Between the first generation
finishing and the generation that actually produced audio there is tool execution, plus any
further generations, and that time is part of the caller's wait::

    hold + llm + continuation + tts == ttfa

``continuation`` is zero for an ordinary turn, so the identity is unchanged there. It is
deliberately not called ``tool``: it contains tool execution *and* intermediate generation,
and the wire cannot separate the two.

Note that the agent may speak *before* calling a tool. In that case the first audio arrives
from the first response, ``continuation`` is zero, and the tool time falls after TTFA where
it belongs, because the caller was already being spoken to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

SPEECH_STOPPED = "input_audio_buffer.speech_stopped"
TRANSCRIPT_DONE = "conversation.item.input_audio_transcription.completed"
RESPONSE_CREATED = "response.created"
RESPONSE_DONE = "response.done"
AUDIO_DELTA = "response.output_audio.delta"
CAPTURE_TIMEOUT = "_capture.timeout"


class TurnOutcome(str, Enum):
    """What a turn turned out to be. Every turn gets one; none are dropped."""

    RESPONDED = "responded"
    """The caller spoke and heard a reply. **Guaranteed to have a defined TTFA**, which is
    why a response with no preceding speech is classified AGENT_INITIATED instead."""

    SILENT = "silent"
    """A response was created and never produced audio. The caller finished speaking and
    heard nothing. TTFA is undefined, and excluding these without counting them is how a
    latency table ends up flattering the system it describes."""

    HELD = "held"
    """The caller spoke and no response was ever created. The endpointer was still waiting
    when the capture ended. TTFA is undefined; the quantity of interest is the wait."""

    AGENT_INITIATED = "agent_initiated"
    """A response with no caller speech in front of it, so it answers nothing and TTFA is
    undefined by construction rather than by failure.

    Same rule as the LiveKit half of this lab reached by a different route: audio with no
    preceding end of speech was not a reply. s2s sends no greeting by default, so this did
    not occur in the reference corpus. It exists so that RESPONDED can carry a real
    guarantee: **every RESPONDED turn has a defined TTFA.** Without it the outcome would be
    a label rather than a promise."""


@dataclass
class Turn:
    """One turn, as the caller experienced it.

    Timestamps are seconds from the start of the capture, taken from the recorded ``t`` of
    each event. Never from a position in a list.
    """

    outcome: TurnOutcome
    speech_stopped_t: float | None = None
    """The **last** speech stop before the response. Where the caller's wait begins."""

    transcript_completed_t: float | None = None
    """The **last** completed transcript before the response."""

    response_created_t: float | None = None
    """The **first** response of the turn. A tool turn has several."""

    audio_response_created_t: float | None = None
    """The response that actually produced the first audio. Equals
    :attr:`response_created_t` unless a tool ran first."""

    first_audio_t: float | None = None

    responses: int = 0
    """Responses in this turn. More than one means a tool ran, or the model continued."""

    segments: int = 0
    """Speech segments the VAD found in this turn. More than one is the common case."""

    revisions: int = 0
    """Distinct input items whose transcript completed. Each supersedes the last, and each
    costs LLM work that is then thrown away."""

    final_transcript: str | None = None
    response_id: str | None = None
    last_wait_s: float | None = None
    """For a HELD turn, how long the capture waited before giving up."""

    @property
    def hold(self) -> float | None:
        """Last speech stop to last transcript. Contains the endpointer's deliberate delay."""
        if self.speech_stopped_t is None or self.transcript_completed_t is None:
            return None
        return self.transcript_completed_t - self.speech_stopped_t

    @property
    def llm(self) -> float | None:
        """Last transcript to response creation. Total generation, not time to first token."""
        if self.transcript_completed_t is None or self.response_created_t is None:
            return None
        return self.response_created_t - self.transcript_completed_t

    @property
    def continuation(self) -> float | None:
        """First response to the response that produced audio.

        Zero on an ordinary turn. On a tool turn it holds the tool execution plus any
        intermediate generation, which the wire cannot separate.
        """
        if self.response_created_t is None or self.audio_response_created_t is None:
            return None
        return self.audio_response_created_t - self.response_created_t

    @property
    def tts(self) -> float | None:
        """The audio-producing response's creation to its first audio byte."""
        if self.audio_response_created_t is None or self.first_audio_t is None:
            return None
        return self.first_audio_t - self.audio_response_created_t

    @property
    def ttfa(self) -> float | None:
        """Time to first audio, from the caller's last speech stop. The number they feel.

        ``None`` unless the turn both ended with a speech stop and produced audio. A response
        with no preceding speech has no point to measure from, and one with no audio has
        nothing to measure to.
        """
        if self.speech_stopped_t is None or self.first_audio_t is None:
            return None
        return self.first_audio_t - self.speech_stopped_t


@dataclass
class _Input:
    """Input-side events accumulated while waiting for the response they caused."""

    stops: list[float] = field(default_factory=list)
    transcripts: list[tuple[float, str, str]] = field(default_factory=list)

    def add(self, t: float, kind: str, ev: dict) -> None:
        if kind == SPEECH_STOPPED:
            self.stops.append(t)
        elif kind == TRANSCRIPT_DONE:
            self.transcripts.append((t, ev.get("item_id", ""), ev.get("transcript", "")))

    def __bool__(self) -> bool:
        return bool(self.stops or self.transcripts)

    def apply(self, turn: Turn) -> None:
        turn.segments = len(self.stops)
        turn.revisions = len({item_id for _, item_id, _ in self.transcripts})
        if self.stops:
            turn.speech_stopped_t = self.stops[-1]
        if self.transcripts:
            turn.transcript_completed_t = self.transcripts[-1][0]
            turn.final_transcript = self.transcripts[-1][2]


def correlate(events: list[dict[str, Any]]) -> list[Turn]:
    """Group a wire capture into turns.

    Args:
        events: Lines from a capture, each ``{"t": float, "type": str, "event": dict}``.
            The ``_capture.meta`` header line may be included or omitted.

    Returns:
        One :class:`Turn` per turn the caller experienced, plus one per stretch of caller
        speech that never received a response. Ordered by when each turn began.
    """
    turns: list[Turn] = []
    pending = _Input()
    open_response: Turn | None = None
    current: Turn | None = None
    """A finished turn, held back in case a continuation response follows it."""

    def close(turn: Turn | None) -> None:
        if turn is not None:
            turns.append(turn)

    # Sort by recorded time rather than trusting file order. The capture writes in arrival
    # order and that is normally the same thing, but the measurement lives in `t`, so `t` is
    # what orders it.
    for ev in sorted((e for e in events if e.get("type") != "_capture.meta"),
                     key=lambda e: e["t"]):
        t, kind, body_ = ev["t"], ev["type"], ev.get("event", {})

        # Input always accumulates into `pending`, which is reset the moment a response is
        # created. Speech arriving *during* a response is a barge-in and belongs to the next
        # turn, not the one being spoken.
        if kind in (SPEECH_STOPPED, TRANSCRIPT_DONE):
            pending.add(t, kind, body_)

        elif kind == RESPONSE_CREATED:
            if open_response is not None:
                # No `response.done` closed the previous one. Keep it rather than lose it and
                # treat this as a continuation of the same turn.
                open_response.responses += 1
                if open_response.first_audio_t is None:
                    open_response.audio_response_created_t = t
            elif current is not None and not pending:
                # A response with no caller speech since the last one is a continuation: the
                # model called a tool, or carried on speaking. The caller spoke once and
                # waited once, so this is still their turn.
                open_response, current = current, None
                open_response.responses += 1
                if open_response.first_audio_t is None:
                    open_response.audio_response_created_t = t
            else:
                close(current)
                current = None
                open_response = Turn(
                    outcome=TurnOutcome.SILENT,
                    response_created_t=t,
                    audio_response_created_t=t,
                    responses=1,
                    response_id=(body_.get("response") or {}).get("id"),
                )
                pending.apply(open_response)
                pending = _Input()

        elif kind == AUDIO_DELTA:
            target = open_response if open_response is not None else current
            if target is not None and target.first_audio_t is None:
                target.first_audio_t = t

        elif kind == RESPONSE_DONE:
            if open_response is not None:
                # Held rather than emitted: a continuation may still follow, and only the
                # arrival of new caller speech proves the turn is over.
                current = _finish(open_response)
                open_response = None

        # The capture gave up waiting. If the caller spoke and nothing answered, that is a
        # held turn, and it is counted rather than discarded.
        elif kind == CAPTURE_TIMEOUT and open_response is None and pending:
            close(current)
            current = None
            held = Turn(outcome=TurnOutcome.HELD, last_wait_s=body_.get("waited_s"))
            pending.apply(held)
            turns.append(held)
            pending = _Input()

    if open_response is not None:
        close(_finish(open_response))
    else:
        close(current)
    if pending:
        # Capture ended mid-turn. Still a held turn.
        held = Turn(outcome=TurnOutcome.HELD)
        pending.apply(held)
        turns.append(held)

    return turns


def _finish(turn: Turn) -> Turn:
    """Settle a response's outcome once nothing more can arrive for it.

    Order matters. A response with no caller speech in front of it is agent-initiated
    whether or not it produced audio, because there is nothing it could be answering.
    Only then does the presence of audio separate RESPONDED from SILENT.
    """
    if turn.speech_stopped_t is None:
        turn.outcome = TurnOutcome.AGENT_INITIATED
    elif turn.first_audio_t is not None:
        turn.outcome = TurnOutcome.RESPONDED
    else:
        turn.outcome = TurnOutcome.SILENT
    return turn

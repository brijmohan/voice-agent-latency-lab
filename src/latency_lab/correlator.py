"""Turn correlation for a cascaded LiveKit voice agent.

The SDK never reports "this turn took N seconds". It reports fragments as each component
finishes, and you reassemble them::

    EOUMetrics  speech_id='speech_abc'  end_of_utterance_delay=0.578  transcription_delay=0.361
    LLMMetrics  speech_id='speech_abc'  ttft=0.534
    TTSMetrics  speech_id='speech_abc'  ttfb=0.135

Three fragments, one turn, one shared key. The caller waited 0.578 + 0.534 + 0.135.

Derived quantities (``end_of_utterance_delay`` verified inclusive of
``transcription_delay``, 11/11 turns, 24 Aug 2026)::

    endpointing_delay = end_of_utterance_delay - transcription_delay
    TTFA              = end_of_utterance_delay + llm_ttft + tts_ttfb

``STTMetrics`` / ``VADMetrics`` / ``EOTInferenceMetrics`` carry no ``speech_id``: STT and
VAD run continuously over the input stream and are not scoped to an agent utterance. They
cannot be joined on this key and are ignored.

Not every ``speech_id`` is a turn
---------------------------------

This is the part that decides whether the published latency table is honest. On the 24 Aug
session, 21 distinct ``speech_id`` values produced 10 usable turns:

===================  ===  =========================================================
Shape                 n   What it actually was
===================  ===  =========================================================
EOU + LLM + TTS       10  a turn the caller experienced. TTFA defined.
LLM only               9  preemptive generations, discarded, none cancelled
LLM + TTS              1  the greeting, created by ``generate_reply`` from our code
EOU + LLM              1  the caller finished speaking and never heard anything
===================  ===  =========================================================

An implementation that emits only the first row and silently discards the rest reports a
latency distribution that excludes its own worst outcome, because the turn that produced no
audio at all is the one the caller minded most. It also hides that this configuration spent
roughly twice the LLM calls it had turns.

So every ``speech_id`` is classified into a :class:`TurnOutcome` and counted. Only
:attr:`TurnOutcome.RESPONDED` reaches :attr:`TurnCorrelator.turns` and therefore the
percentile table; everything else appears in :meth:`TurnCorrelator.finalize`.

Classification is terminal, not live
------------------------------------

You cannot know a turn produced no audio until the session ends, because the audio might
still be coming. So ``RESPONDED`` is emitted live, the moment its three fragments are in,
and every other outcome is decided once at :meth:`TurnCorrelator.finalize`.

Agent-initiated turns, and why ``user_initiated`` cannot be used for this
------------------------------------------------------------------------

``SpeechCreatedEvent`` looks like it answers "did the agent start this?" and in a cascaded
pipeline it does not. Verified against livekit-agents 1.8.0 by running it, after a first
attempt that read the emit sites and got it wrong:

* ``AgentActivity._generate_reply`` (``agent_activity.py:1605``) takes **no**
  ``user_initiated`` parameter and hardcodes ``user_initiated=True`` at line 1660.
* Both pipeline paths call it: line 2417 for a preemptive generation, line 2670 for an
  ordinary completed user turn.
* The only ``user_initiated=False`` emit site, line 2106, is ``_on_generation_created``,
  which is the **realtime model** path and never runs in a cascaded pipeline.

So every speech in a cascaded session reports ``user_initiated=True``, including ordinary
replies. Classifying on it marks all of them agent-initiated and empties the latency table.
That is what a live run produced: 16 speech_ids, 16 agent-initiated, zero turns.

``source`` does not discriminate either, because the greeting uses ``generate_reply`` like
every other turn, and neither does ``input_details``, since
``DEFAULT_INPUT_DETAILS = InputDetails(modality="audio")`` applies to both.

**The signal the SDK actually gives is the end-of-utterance itself.** A speech that produced
audio with no preceding EOU had no user utterance in front of it, which is exactly what
agent-initiated means. There is no ambiguity with a failed turn: ``EOUMetrics`` is emitted
when the user turn completes (line 2699), and a turn that never completed never triggers a
reply, so "audio, no EOU" cannot be a transcription failure.

``source == "say"`` is kept as a second positive signal, because a ``say()`` call is
definitely not a reply to anything. ``user_initiated`` is recorded and deliberately not used.

Retries are not slowness
------------------------

On 24 Aug one turn recorded ``ttft=16.350`` with ``cancelled=False`` and every other stage
normal. It was a provider 504 followed by a retry cycle. A latency table that cannot
separate "slow" from "retried" is measuring provider availability while claiming to measure
model latency, so two independent signals are recorded when available:

1. ``MetricsReport.provider_request_ids`` (stream C) with more than one entry means the
   turn was retried. This is direct evidence.
2. ``llm_ttft`` greater than the LLM plugin's per-attempt ``APIConnectOptions.timeout``
   means at least one attempt timed out, because a single attempt cannot exceed it. This
   is an inference, and it is only available if you tell the correlator the timeout you
   configured. It is not assumed, because hardcoding a default that the deployment did not
   set is how the last four retracted claims in CORRECTIONS.md happened.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from livekit.agents.metrics import EOUMetrics, LLMMetrics, TTSMetrics


class TurnOutcome(str, Enum):
    """What a ``speech_id`` turned out to be, decided at :meth:`TurnCorrelator.finalize`."""

    RESPONDED = "responded"
    """EOU + LLM + TTS. The caller spoke and heard a reply. TTFA is defined and this is the
    only outcome that belongs in the latency distribution."""

    SILENT = "silent"
    """EOU + LLM but no audio. The caller finished speaking and heard nothing back. TTFA is
    undefined, and excluding these without counting them is what makes a latency table
    flatter than the system it describes."""

    AGENT_INITIATED = "agent_initiated"
    """Created by ``say`` or the public ``generate_reply`` (``user_initiated=True``). The
    greeting is the usual case. No preceding end-of-utterance exists, so TTFA is undefined
    by construction rather than by failure."""

    SPECULATIVE = "speculative"
    """LLM only, never committed to a turn. ``preemptive_generation`` is on by default and
    fires the LLM before the end of turn is decided; when the caller keeps talking the
    generation is discarded and its ``speech_id`` never receives an EOU. These are real LLM
    spend and no part of any turn."""

    INCOMPLETE = "incomplete"
    """Anything else still in flight at shutdown. Expected to be zero or near it. A non-zero
    count is a signal to go and look, not a category to ignore."""


@dataclass
class TurnRecord:
    """One ``speech_id``, with whatever stages it actually produced.

    ``llm_ttft`` and ``tts_ttfb`` are optional because not every outcome has them. Only
    :attr:`TurnOutcome.RESPONDED` guarantees all four timing fields, and only that outcome
    has a :attr:`ttfa`.
    """

    speech_id: str
    outcome: TurnOutcome
    end_of_utterance_delay: float | None = None
    transcription_delay: float | None = None
    llm_ttft: float | None = None
    tts_ttfb: float | None = None

    llm_calls: int = 0
    """LLM requests seen for this ``speech_id``. More than one means tool steps: the model
    was called again after a tool returned. Only the first is on the path to first audio."""

    tts_segments: int = 0
    """TTS segments synthesised. Later segments stream behind the first, so only the first
    contributes to the wait, but the count is kept because a turn split into many segments
    behaves differently under interruption."""

    llm_request_ids: list[str] = field(default_factory=list)
    """``LLMMetrics.request_id`` per call, in arrival order. Client-side ids."""

    provider_request_ids: list[str] = field(default_factory=list)
    """From ``MetricsReport`` (stream C), if wired. Provider-side ids for this turn. More
    than one is direct evidence that the request was retried."""

    retry_evidence: str | None = None
    """Why this turn is believed to have been retried, or ``None``. Recorded as a reason
    rather than a boolean so the table can say what the evidence was."""

    @property
    def endpointing_delay(self) -> float | None:
        """The turn detector's own contribution, net of waiting for the transcript."""
        if self.end_of_utterance_delay is None or self.transcription_delay is None:
            return None
        return self.end_of_utterance_delay - self.transcription_delay

    @property
    def ttfa(self) -> float | None:
        """Time to first audible response, the number the caller actually feels.

        ``None`` for every outcome except :attr:`TurnOutcome.RESPONDED`, because a turn that
        produced no audio has no time-to-audio and a turn with no preceding end-of-utterance
        has no point to measure from.
        """
        if self.end_of_utterance_delay is None or self.llm_ttft is None or self.tts_ttfb is None:
            return None
        return self.end_of_utterance_delay + self.llm_ttft + self.tts_ttfb

    @property
    def retried(self) -> bool:
        """True when there is evidence the underlying request was retried."""
        return self.retry_evidence is not None


@dataclass
class SessionSummary:
    """The full ledger for a session: every ``speech_id``, classified.

    :attr:`records` contains one entry per ``speech_id`` ever seen, including the responded
    turns already delivered through :attr:`TurnCorrelator.turns`.
    """

    records: list[TurnRecord]

    @property
    def counts(self) -> dict[TurnOutcome, int]:
        """How many ``speech_id`` values fell into each outcome."""
        out = dict.fromkeys(TurnOutcome, 0)
        for r in self.records:
            out[r.outcome] += 1
        return out

    @property
    def responded(self) -> list[TurnRecord]:
        """The turns with a defined TTFA. The latency distribution is computed over these."""
        return [r for r in self.records if r.outcome is TurnOutcome.RESPONDED]

    @property
    def llm_calls_per_responded_turn(self) -> float | None:
        """Total LLM requests divided by turns the caller actually heard.

        On the 24 Aug session this was 2.1, because ``preemptive_generation`` is on by
        default. It is the cost of the latency saving and it is not reported anywhere else.
        """
        responded = len(self.responded)
        if responded == 0:
            return None
        return sum(r.llm_calls for r in self.records) / responded

    def format_report(self) -> str:
        """A short accounting suitable for printing at shutdown."""
        counts = self.counts
        lines = [f"[correlator] {len(self.records)} speech_id(s) seen:"]
        for outcome in TurnOutcome:
            n = counts[outcome]
            if n:
                lines.append(f"[correlator]   {outcome.value:<16} {n}")

        ratio = self.llm_calls_per_responded_turn
        if ratio is not None:
            lines.append(f"[correlator]   LLM calls per responded turn: {ratio:.1f}")

        retried = [r for r in self.records if r.retried]
        for r in retried:
            lines.append(f"[correlator]   retried: {r.speech_id} ({r.retry_evidence})")

        for r in self.records:
            if r.outcome in (TurnOutcome.SILENT, TurnOutcome.INCOMPLETE):
                lines.append(f"[correlator]   {r.outcome.value}: {r.speech_id}")
        return "\n".join(lines)


@dataclass
class _TurnState:
    """Mutable accumulator for one ``speech_id`` while the session is running."""

    fields: dict[str, Any] = field(default_factory=dict)
    llm_calls: int = 0
    tts_segments: int = 0
    llm_request_ids: list[str] = field(default_factory=list)
    user_initiated: bool | None = None
    source: str | None = None
    handle: Any = None


class TurnCorrelator:
    """Accepts metric fragments in arrival order, emits one record per completed turn.

    Correctness must not depend on arrival order. Components finish in an order that varies
    with network and load, so TTS can report before the LLM does, and two turns can be in
    flight at once. Every join is therefore keyed, never positional.

    Args:
        on_turn: Called with each :class:`TurnRecord` the moment it becomes
            :attr:`TurnOutcome.RESPONDED`. Live callback; every other outcome is only known
            at :meth:`finalize`.
        llm_timeout: The per-attempt ``APIConnectOptions.timeout`` configured on the LLM
            plugin, in seconds. Supply it to enable retry inference: a ``ttft`` above it
            cannot come from a single attempt. Left ``None`` the inference is simply not
            made, because assuming the SDK default when the deployment set something else
            would be a fabricated finding.
    """

    _REQUIRED = ("end_of_utterance_delay", "llm_ttft", "tts_ttfb")
    """Fields a record needs before TTFA can be computed."""

    def __init__(
        self,
        on_turn: Callable[[TurnRecord], None] | None = None,
        *,
        llm_timeout: float | None = None,
    ) -> None:
        self._state: dict[str, _TurnState] = {}
        self._emitted: set[str] = set()
        self._chat_metrics: dict[str, dict] = {}
        self.turns: list[TurnRecord] = []
        self._on_turn = on_turn
        self._llm_timeout = llm_timeout

    # --- stream A: raw per-component metrics ---------------------------------
    def on_metric(self, metric: Any) -> None:
        """Feed one ``MetricsCollectedEvent.metrics`` object.

        Metrics without a ``speech_id`` are ignored: STT and VAD are not scoped to a turn
        and there is nothing to join them on. Metric types this correlator does not model
        are ignored *before* any state is created for them, so an unmodelled type carrying a
        ``speech_id`` cannot manufacture a phantom incomplete turn at shutdown.
        """
        speech_id = getattr(metric, "speech_id", None)
        if speech_id is None:
            return

        if not isinstance(metric, (EOUMetrics, LLMMetrics, TTSMetrics)):
            return

        if speech_id in self._emitted:
            # Already delivered as a responded turn. A late second TTS segment lands here;
            # count it, but do not re-emit and do not disturb the recorded ttfb.
            if isinstance(metric, TTSMetrics):
                self._state[speech_id].tts_segments += 1
            elif isinstance(metric, LLMMetrics):
                self._state[speech_id].llm_calls += 1
                self._state[speech_id].llm_request_ids.append(metric.request_id)
            return

        state = self._state.setdefault(speech_id, _TurnState())

        if isinstance(metric, EOUMetrics):
            state.fields["end_of_utterance_delay"] = metric.end_of_utterance_delay
            state.fields["transcription_delay"] = metric.transcription_delay
        elif isinstance(metric, LLMMetrics):
            state.llm_calls += 1
            state.llm_request_ids.append(metric.request_id)
            # First call wins. A turn with tool steps produces several; the later ones are
            # not what the caller waited for before hearing anything.
            state.fields.setdefault("llm_ttft", metric.ttft)
        elif isinstance(metric, TTSMetrics):
            state.tts_segments += 1
            # First segment only. Later segments stream behind the first, so counting them
            # would overstate the wait.
            state.fields.setdefault("tts_ttfb", metric.ttfb)

        self._emit_if_responded(speech_id)

    def _emit_if_responded(self, speech_id: str) -> None:
        """Emit as soon as the three timing fields are in, if this can be a turn at all."""
        state = self._state[speech_id]

        # A say() is never a reply to anything, so it can never be a responded turn.
        # Deliberately NOT guarding on user_initiated: in a cascaded pipeline it is True for
        # every speech, and guarding on it empties the table. See the module docstring.
        if state.source == "say":
            return

        if not all(k in state.fields for k in self._REQUIRED):
            return

        record = self._build_record(speech_id, TurnOutcome.RESPONDED)
        self._emitted.add(speech_id)
        self.turns.append(record)
        if self._on_turn is not None:
            self._on_turn(record)

    def _build_record(self, speech_id: str, outcome: TurnOutcome) -> TurnRecord:
        state = self._state[speech_id]
        record = TurnRecord(
            speech_id=speech_id,
            outcome=outcome,
            end_of_utterance_delay=state.fields.get("end_of_utterance_delay"),
            transcription_delay=state.fields.get("transcription_delay"),
            llm_ttft=state.fields.get("llm_ttft"),
            tts_ttfb=state.fields.get("tts_ttfb"),
            llm_calls=state.llm_calls,
            tts_segments=state.tts_segments,
            llm_request_ids=list(state.llm_request_ids),
        )
        self._apply_retry_evidence(record)
        return record

    def _apply_retry_evidence(self, record: TurnRecord) -> None:
        """Record why a turn looks retried, using direct evidence before inference."""
        if len(record.provider_request_ids) > 1:
            record.retry_evidence = f"{len(record.provider_request_ids)} provider request ids"
            return
        # A single connection attempt cannot exceed the per-attempt timeout, so a ttft above
        # it means at least one attempt timed out and was retried. Only available when the
        # configured timeout was supplied; never guessed.
        if (
            self._llm_timeout is not None
            and record.llm_ttft is not None
            and record.llm_ttft > self._llm_timeout
        ):
            record.retry_evidence = (
                f"ttft {record.llm_ttft:.3f}s exceeds per-attempt timeout {self._llm_timeout:.1f}s"
            )

    # --- stream B: speech lifecycle ------------------------------------------
    def on_speech_created(
        self,
        *,
        speech_id: str,
        user_initiated: bool,
        source: Literal["say", "generate_reply"],
        handle: Any,
    ) -> None:
        """Record how a speech came into existence.

        This is the only deterministic way to tell an agent-initiated speech from a reply to
        the caller. Inferring it from a missing EOU cannot work, because a turn that failed
        to transcribe also has no EOU and is a completely different event.

        ``user_initiated`` is recorded but **not** used for classification: in a cascaded
        pipeline it is ``True`` for every speech, including ordinary replies. See the module
        docstring for the call paths that prove it. ``source`` is used, because ``"say"``
        is unambiguous.

        ``handle`` is retained so :meth:`finalize` can walk ``handle.chat_items`` to map
        assistant message ids back to this ``speech_id``. It is read late, at shutdown,
        because the list is populated as the reply is generated.
        """
        state = self._state.setdefault(speech_id, _TurnState())
        state.user_initiated = user_initiated
        state.source = source
        state.handle = handle

    # --- stream C: the framework's own per-turn report -----------------------
    def on_chat_message(
        self,
        *,
        message_id: str,
        role: str,
        metrics: dict,
        interrupted: bool,
    ) -> None:
        """Stash a ``MetricsReport`` for later joining.

        ``MetricsReport`` is split across two messages with different ids: the user
        ``ChatMessage`` carries ``transcription_delay`` and ``end_of_turn_delay``, the
        assistant one carries ``llm_node_ttft``, ``tts_node_ttfb``, ``e2e_latency`` and
        ``provider_request_ids``. Neither carries a ``speech_id``, so the join is deferred to
        :meth:`finalize`, where the speech handle can supply it.

        Only assistant messages are kept, since ``provider_request_ids`` is assistant-only.
        """
        if role != "assistant" or not metrics:
            return
        self._chat_metrics[message_id] = dict(metrics)

    def _attach_provider_ids(self, speech_id: str, record: TurnRecord) -> None:
        """Join stream C onto a record via ``handle.chat_items``.

        Guarded throughout: this reaches into SDK internals that are not part of the public
        surface, and a correlator that raises at shutdown loses the whole session's report
        to save a field nobody strictly needs.
        """
        handle = self._state[speech_id].handle
        if handle is None:
            return
        try:
            items = list(getattr(handle, "chat_items", []) or [])
        except (AttributeError, TypeError):
            return
        for item in items:
            report = self._chat_metrics.get(getattr(item, "id", None))
            if not report:
                continue
            for rid in report.get("provider_request_ids", []) or []:
                if rid not in record.provider_request_ids:
                    record.provider_request_ids.append(rid)

    # --- lifecycle ------------------------------------------------------------
    def _classify(self, speech_id: str) -> TurnOutcome:
        """Decide what a ``speech_id`` was, once nothing more is coming for it."""
        state = self._state[speech_id]
        fields = state.fields
        has_eou = "end_of_utterance_delay" in fields
        has_tts = "tts_ttfb" in fields
        has_llm = "llm_ttft" in fields

        if state.source == "say":
            # An explicit say(). Not a reply to anything, by definition.
            return TurnOutcome.AGENT_INITIATED
        if has_eou and has_llm and has_tts:
            return TurnOutcome.RESPONDED
        if has_tts and not has_eou:
            # Audio with no preceding end of utterance. The greeting is this. Not reachable
            # by a failed turn: no completed user turn means no EOU and also no reply.
            return TurnOutcome.AGENT_INITIATED
        if has_eou:
            # The caller finished speaking and no audio was ever produced. Whether the LLM
            # ran or not, the caller's experience was silence.
            return TurnOutcome.SILENT
        if has_llm and not has_tts:
            # No end of utterance ever arrived, so this generation was never committed to a
            # turn. preemptive_generation produces these whenever the caller keeps talking.
            return TurnOutcome.SPECULATIVE
        return TurnOutcome.INCOMPLETE

    def finalize(self) -> SessionSummary:
        """Classify every ``speech_id`` seen and return the session ledger.

        Call once, at shutdown. Idempotent in the sense that it recomputes from retained
        state rather than consuming it, so calling it twice returns the same summary.
        """
        records: list[TurnRecord] = []
        emitted_by_id = {r.speech_id: r for r in self.turns}

        for speech_id in self._state:
            outcome = self._classify(speech_id)
            record = emitted_by_id.get(speech_id)
            if record is None:
                record = self._build_record(speech_id, outcome)
            else:
                # Already delivered live. Refresh the counters, which may have advanced
                # after emission (a late TTS segment, a tool step), without rebuilding the
                # timings the caller has already been given.
                state = self._state[speech_id]
                record.llm_calls = state.llm_calls
                record.tts_segments = state.tts_segments
                record.llm_request_ids = list(state.llm_request_ids)

            self._attach_provider_ids(speech_id, record)
            self._apply_retry_evidence(record)
            records.append(record)

        return SessionSummary(records=records)

    def flush_incomplete(self) -> SessionSummary:
        """Print the session ledger and return it.

        Kept under its original name because the shutdown callback in ``agent.py`` calls it.
        Silently discarded turns are how a latency table ends up flattering itself, so this
        reports every outcome rather than only the ones that fit the table.
        """
        summary = self.finalize()
        print(summary.format_report())
        return summary

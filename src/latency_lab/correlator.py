"""Turn correlation for lab01.

HAPPY PATH ONLY. Written 31 Aug 2026 at Brij's explicit request. The six edge cases
below are deliberately NOT handled yet; see the end of this docstring.

The problem: the SDK never reports "this turn took N seconds". It reports fragments as
each component finishes, and you reassemble them.

    EOUMetrics  speech_id='speech_abc'  end_of_utterance_delay=0.578  transcription_delay=0.361
    LLMMetrics  speech_id='speech_abc'  ttft=0.534
    TTSMetrics  speech_id='speech_abc'  ttfb=0.135

Three fragments, one turn, one shared key. The caller waited 0.578 + 0.534 + 0.135.

Derived (eou verified inclusive of transcription_delay, 11/11 turns, 24 Aug):

    endpointing_delay = end_of_utterance_delay - transcription_delay
    TTFA              = end_of_utterance_delay + llm_ttft + tts_ttfb

STTMetrics / VADMetrics / EOTInferenceMetrics carry NO speech_id: STT and VAD run
continuously over the input stream and are not scoped to an agent utterance. They cannot
be joined and are ignored here.

NOT HANDLED YET, each one a decision to make and defend:

  1. Turns that never produce audio (EOU, no TTS). Observed 1 in 11 on 24 Aug.
     Currently: silently never emitted. That is the flattering-table failure.
  2. Agent-initiated turns (TTS, no EOU) such as the greeting. Observed 1 in 11.
     Currently: silently never emitted. Should be classified via
     speech_created.user_initiated, not inferred from a missing EOU.
  3. preemptive_generation orphans. 24 Aug log, 21 distinct speech_ids:
       10  EOU + LLM + TTS   complete turns
        9  LLM only          speculative generations, discarded, none cancelled
        1  LLM + TTS         the greeting (agent-initiated)
        1  EOU + LLM         a turn that never produced audio
     Currently: the 9 land in _pending and never complete. Count them; they are
     ~2x your LLM spend and nobody publishes that number.
  4. Multiple TTS segments per turn. Currently: first wins, later ignored.
  5. Multiple LLM calls per turn (tool steps). Currently: first wins.
  6. Retried requests. ttft=16.350 with cancelled=False on 24 Aug was a Google 504 plus
     a retry cycle. Indistinguishable from a slow model here.
     provider_request_ids on the assistant MetricsReport is the better signal.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from livekit.agents.metrics import EOUMetrics, LLMMetrics, TTSMetrics


@dataclass
class TurnRecord:
    """One conversational turn, as the caller experienced it."""

    speech_id: str
    end_of_utterance_delay: float
    transcription_delay: float
    llm_ttft: float
    tts_ttfb: float

    @property
    def endpointing_delay(self) -> float:
        """The turn detector's own contribution, net of waiting for the transcript."""
        return self.end_of_utterance_delay - self.transcription_delay

    @property
    def ttfa(self) -> float:
        """Time to first audible response. The number the caller actually feels."""
        return self.end_of_utterance_delay + self.llm_ttft + self.tts_ttfb


class TurnCorrelator:
    """Accepts metric fragments in arrival order, emits one record per completed turn.

    Correctness must not depend on arrival order: components finish in an order that
    varies with network and load, so TTS can report before the LLM does.
    """

    #: fields a record needs before TTFA can be computed
    _REQUIRED = ("end_of_utterance_delay", "llm_ttft", "tts_ttfb")

    def __init__(self, on_turn: Callable[[TurnRecord], None] | None = None) -> None:
        self._pending: dict[str, dict[str, Any]] = {}
        self._completed: set[str] = set()
        self.turns: list[TurnRecord] = []
        self._on_turn = on_turn

    # --- stream A: raw per-component metrics ---------------------------------
    def on_metric(self, metric: Any) -> None:
        speech_id = getattr(metric, "speech_id", None)
        if speech_id is None:
            return  # STT / VAD / EOT: no join key, nothing to correlate

        if speech_id in self._completed:
            return  # already emitted; a late second TTS segment lands here

        fields = self._pending.setdefault(speech_id, {})

        if isinstance(metric, EOUMetrics):
            fields["end_of_utterance_delay"] = metric.end_of_utterance_delay
            fields["transcription_delay"] = metric.transcription_delay
        elif isinstance(metric, LLMMetrics):
            # First call wins. A turn with tool steps produces several; later ones are
            # not what the caller waited for before hearing anything.
            fields.setdefault("llm_ttft", metric.ttft)
        elif isinstance(metric, TTSMetrics):
            # First segment only. Later segments stream behind the first, so counting
            # them would overstate the wait.
            fields.setdefault("tts_ttfb", metric.ttfb)
        else:
            return

        self._emit_if_complete(speech_id)

    def _emit_if_complete(self, speech_id: str) -> None:
        fields = self._pending[speech_id]
        if not all(k in fields for k in self._REQUIRED):
            return

        record = TurnRecord(
            speech_id=speech_id,
            end_of_utterance_delay=fields["end_of_utterance_delay"],
            transcription_delay=fields["transcription_delay"],
            llm_ttft=fields["llm_ttft"],
            tts_ttfb=fields["tts_ttfb"],
        )
        del self._pending[speech_id]
        self._completed.add(speech_id)
        self.turns.append(record)
        if self._on_turn is not None:
            self._on_turn(record)

    # --- stream C: speech lifecycle ------------------------------------------
    def on_speech_created(
        self,
        *,
        speech_id: str,
        user_initiated: bool,
        source: Literal["say", "generate_reply"],
        handle: Any,
    ) -> None:
        """Not used by the happy path. Wire this to classify agent-initiated turns."""
        return

    # --- stream B: the framework's own per-turn report -----------------------
    def on_chat_message(
        self,
        *,
        message_id: str,
        role: str,
        metrics: dict,
        interrupted: bool,
    ) -> None:
        """Not used by the happy path. Wire this to validate against MetricsReport."""
        return

    # --- lifecycle ------------------------------------------------------------
    def flush_incomplete(self) -> None:
        """Report what was still in flight. Silently discarded turns are how a latency
        table ends up flattering itself."""
        if not self._pending:
            return
        print(f"[correlator] {len(self._pending)} incomplete turn(s) at shutdown:")
        for speech_id, fields in self._pending.items():
            missing = [k for k in self._REQUIRED if k not in fields]
            print(f"[correlator]   {speech_id} missing={missing} have={sorted(fields)}")

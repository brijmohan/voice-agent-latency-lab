# voice-agent-latency-lab

**Where the 800ms goes in a cascaded voice agent, decomposed into four stages and reported
at the tail.**

A caller finishes a sentence and waits. This measures that wait, splits it into the four
stages that produce it, and reports the distribution rather than the average.

```
TTFA = end_of_utterance_delay + llm_ttft + tts_ttfb
       └──────────┬──────────┘  └───┬───┘  └───┬───┘
        transcription + endpointing  LLM      TTS
```

The framework reports per component. A caller experiences per turn. Nothing bridges the
two, so this does.

---

## Results

LiveKit Agents 1.7.0, Deepgram nova-3, Gemini 3.5 Flash Lite (direct, not via gateway),
Cartesia sonic-3, Silero VAD, LiveKit audio turn detector v1. macOS, M3 Pro. English.
`endpointing_ms=25`. Console mode.

**n = 10 completed turns**, from one session. Not enough for P99. See *Honest limits*.

| Stage | P50 | P90 | max | share of P50 TTFA |
|---|---|---|---|---|
| `end_of_utterance_delay` | 0.579s | 0.953s | 2.501s | 38% |
| ├─ `transcription_delay` | 0.370s | 0.679s | 0.937s | 24% |
| └─ `endpointing_delay` | 0.209s | 0.328s | 2.152s | 14% |
| `llm_ttft` | 0.605s | 1.279s | 16.350s | 40% |
| `tts_ttfb` | 0.136s | 0.145s | 0.188s | 9% |
| **TTFA** | **1.530s** | **3.145s** | **17.065s** | |

Stage percentiles are marginal distributions. They do not sum to the TTFA percentile,
because the worst turn for one stage is not the worst turn for another. Shares are computed
at P50 only.

### Three things that surprised me

**1. `transcription_delay` varies 4x on one speaker in one session** (0.257s to 1.031s),
at a fixed `endpointing_ms=25`. It is the first stage of the budget, almost nobody tunes it,
and it is the least stable. It is also not 25ms: `endpointing_ms` is one input to the
provider's finalisation decision, not the decision.

**2. The turn detector is acoustic, and near-identical utterances get opposite decisions.**

| Utterance | P(end of turn) | Decision |
|---|---|---|
| "Wait. Wait. Wait. Wait. Wait." | **0.915** | committed in 0.3s |
| "Wait. Wait. Wait. Wait." | **0.068** | held for 2.5s |

Same words, an order of magnitude apart. `inference.TurnDetector()` encodes audio directly
and scores prosody. No transcript-based model can produce that pair. It is also why an agent
feels inconsistent to its operator: the variable is one the speaker cannot hear themselves
changing.

**3. The tail is the provider retrying, not the model being slow.** One turn recorded
`ttft=16.350s` with `cancelled=False` and every other stage normal. It was a 504 and a
retry cycle under `max_retry=3, retry_interval=2.0, timeout=10.0`. Nothing in the metric
object says so; the only evidence was a WARNING line. **A latency table that cannot separate
"slow" from "retried" is measuring provider availability while claiming to measure model
latency.**

### The endpointing floor nobody tunes

`end_of_utterance_delay` pins to ~0.578s on 7 of 10 turns. That floor is not
`endpointing_ms` and not `min_delay`. It is Silero VAD's `min_silence_duration=0.55`,
because `min_delay` behaves as `max(VAD silence, min_delay)`. The audio turn detector
permits 0.25, so there is roughly **280ms available on every fast turn**, behind a
parameter that appears in neither of the two knobs people reach for.

---

## Honest limits

What this does **not** measure, which is what makes the rest worth reading:

- **The network leg.** Metrics stop at publication. Transit and the receiver's adaptive
  jitter buffer are invisible, and the buffer grows on a bad network. Real caller-perceived
  latency is higher than every number here.
- **Console mode has no room.** No WebRTC, no Opus encode/decode, no SFU. Three tiers exist,
  each strictly worse: console, browser over WebRTC, phone. This is the most optimistic.
- **Not everything is local even so.** The turn detector runs on LiveKit Inference and the
  adaptive interruption detector calls `agent-gateway.livekit.cloud`. There is network in
  the turn-taking path, from Lille.
- **n=10.** P99 at n=10 is the maximum, not a percentile. P99 needs n≥100 per configuration,
  which is not reachable by hand. P90 is reported; P99 is not.
- **One speaker, one language, one session.** No claim about anyone else's voice.
- **The greeting is excluded.** Agent-initiated, so no end-of-utterance exists and TTFA is
  undefined. Excluded by construction, counted separately.

## Correlation, and why it is not a `GROUP BY`

`EOUMetrics`, `LLMMetrics` and `TTSMetrics` share a `speech_id`. `STTMetrics`, `VADMetrics`
and `EOTInferenceMetrics` do not, because STT and VAD run continuously over the input and
are not scoped to an utterance.

Six things break the naive join, all observed in a two-minute conversation:

1. Events arrive out of order. Verified independent across 200 shuffles and full reversal.
2. Nothing marks a turn finished. Completeness is defined as "all TTFA fields present".
3. STT and VAD cannot be joined at all.
4. Turns that never produce audio. Excluded, and counted.
5. Agent-initiated turns have no end-of-utterance.
6. **`preemptive_generation` is on by default.** 21 `LLMMetrics` across 11 turns, 21 distinct
   `speech_id`s, none cancelled: the LLM is dispatched speculatively on partial transcripts
   and 9 generations were discarded. Roughly 2x the LLM calls, and the bill.

## Run it

```bash
uv sync
cp .env.example .env.local          # fill in your keys
uv run python src/latency_lab/agent.py console
uv run pytest                        # 11 tests
uv run python tools/replay_log.py <logfile>
```

## What is next

- P90 at n≥100 per configuration, which needs an automated caller.
- The same decomposition against a second architecture that shares no event model.
- French. English STT transcribed "Lambersart" as "Dolombasa" and "mairie" as "Mary",
  which is fine for measurement and disqualifying for anything else.

## Corrections

Claims made here that turned out to be wrong are recorded in [CORRECTIONS.md](CORRECTIONS.md)
rather than quietly edited.

## Licence

Apache-2.0.

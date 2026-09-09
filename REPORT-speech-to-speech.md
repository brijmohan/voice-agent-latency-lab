# Per-turn latency for `huggingface/speech-to-speech`, measured on the wire

**v1.0.0 (`16d7f98`), fully local on an M3 Pro, `--mac-optimal-settings`. n = 115 turns.**

Measured from **outside the process**, over the Realtime API the project already exposes.
No fork, no patch, no vendored copy. The harness connects as an external WebSocket client,
streams recorded human speech at wall-clock pace, and timestamps every server event on
arrival with a monotonic clock.

Stack: Parakeet TDT (mlx), Qwen3-4B-Instruct-2507 4-bit (mlx), Qwen3-TTS-12Hz-1.7B
CustomVoice 6-bit (mlx), Smart Turn v3.2 at its default threshold of 0.50.

---

## Numbers

23 recorded human utterances, replayed 5 times each, one fresh session per turn.

| stage | P50 | P90 | P99 | max |
|---|---|---|---|---|
| `hold` (last speech stop to transcript) | 0.098 | 0.723 | 0.985 | 1.089 |
| `llm` (transcript to `response.created`) | 1.285 | 1.743 | 2.167 | 2.197 |
| `tts` (`response.created` to first audio) | 0.156 | 0.174 | 0.180 | 0.204 |
| **TTFA** (last speech stop to first audio) | **1.577** | **2.298** | **3.192** | **3.334** |

115 of 115 turns responded. Zero silent, zero held, zero unclassified.

Replaying an identical recording gives a median TTFA range of **0.134s** across 5 runs, so
the harness is stable enough to attribute a change to a config rather than to the speaker.

---

## Four things worth knowing

### 1. Endpointing is the one stage the wire cannot see

`input_audio_buffer.speech_stopped` is emitted **after** Smart Turn has already decided. In
the first validated session the server logged its Smart Turn completion at `08.371` and the
wire carried `speech_stopped` at the equivalent of `08.372`. They coincide to about a
millisecond, so the decision is invisible from outside by construction.

The wire gives three of the four stages exactly. The fourth needs instrumentation inside the
process. Smart Turn's own inference cost, 88.6ms on that turn, is only knowable from within.

### 2. `response.created` fires when the LLM finishes, so the wire cannot give TTFT

Not when the turn is committed. Cross-checked against the server's own MLX lock log: a
1.930s gap between `transcription.completed` and `response.created` matched a logged
`MLX-LLM: MLX lock released after holding 1.90s`.

The consequence is that `llm` above is **total generation, not time to first token**. There
is no anchor on the wire that yields TTFT for this stack. Any external tool reporting one
would be inferring it.

### 3. The endpointer holds on half of all decisions, and the hold costs about 600ms

Across the sweep the server made **280 Smart Turn decisions for 115 turns**:

| verdict | n | consequence |
|---|---|---|
| complete | 144 | 800ms speculative reopen grace |
| incomplete | 136 | 2000ms grace, and processing delayed by 600ms |

**136 of 280, 49%, were "hold".** That cost lands in `hold`, not in transcription:

| block | what was said | median `hold` | median TTFA |
|---|---|---|---|
| A, clean sentences with falling prosody | "The first name is Edward Hawkins." | 0.082 | 1.397 |
| B, deliberately unfinished | "The date I was thinking of was" | **0.689** | 2.193 |
| F, one or two words | "Yes." | 0.044 | 1.334 |

Smart Turn classified the unfinished sentences correctly (p=0.011 on one, against p=0.988 on
a clean one). It did not cut the speaker off. It waited, and proceeded only when they did not
resume. The 600ms is the price of being right, and it is invisible in any TTFA average.

### 4. Revisions are a real and unreported LLM cost

An utterance with pauses does not arrive as one item. Each speech segment produces a new
`item_id` whose transcript **restates the whole utterance so far**:

```
"I'd like to book"
"I'd like to book a ceremony slot."
"I'd like to book a ceremony slot for the fourteenth"
```

**61% of turns had more than one speech segment.** Mean 1.63 revisions per turn, max 4. One
utterance, a spelled-out email address, produced **11 speech segments folded into 4
revisions**.

Each superseded revision is LLM work already done. Over the sweep the server logged 192
MLX-LLM lock acquisitions for 115 responded turns. That figure is offered tentatively: the
same log reported only 21 Parakeet acquisitions across 115 turns, which fits no explanation
I can currently defend, so something about that log line is not what it appears.

---

## What this does not measure

- **One speaker, one language, one machine, one configuration.** No claim about anyone else's
  voice, and nothing here says anything about French.
- **The P99 is pooled across 23 different utterances**, so it partly reflects which sentence
  was spoken rather than a system tail. Per-utterance spread (median range 0.134s) is the
  honest variance figure.
- **Replay gives system variance, not population variance.** Right for tuning and regression
  gates, wrong for "what will callers experience".
- **Zero silent turns, zero held turns, zero barge-ins and zero tool turns occurred.** The
  corpus was recorded to a script and the session had no tools. Four code paths are therefore
  carried by synthetic tests alone.
- **No comparison with any other stack.** A cascaded cloud pipeline and a fully local one
  differ by so much more than architecture that a shared table would mislead. This measures
  one system against itself across configurations, which is the only comparison the method
  supports.

## How this relates to PR #539

They measure different things and neither is sufficient alone.

`pipeline/turn_latency.py` in that PR instruments from inside and can attribute endpointing,
which the wire cannot reach. The wire sits where the client sits and sees what the caller
actually got, including transport, which internal instrumentation cannot reach. It also needs
no patch, so it works against any released version and against any other Realtime-compatible
server unchanged.

## What I would measure next

1. **Per-language endpointing.** Smart Turn v3.2 runs on a single global threshold with no
   language conditioning, while the default STT declares 25 languages. LiveKit ships
   per-language thresholds for the same task and `livekit/eot-bench` evaluates Smart Turn
   across 14. The obvious experiment is a threshold sweep reported as **cutoff rate against
   added latency**, as a curve rather than a single operating point.
2. **The same corpus in French**, recorded with matched utterance functions, which makes the
   pair a controlled multilingual comparison rather than two separate runs.
3. **The speculative reopen grace.** 800ms and 2000ms appear in the VAD log and I have not
   read the code path. Any endpointing claim depends on it.

## Reproduce

```bash
speech-to-speech serve --mac-optimal-settings --host 127.0.0.1 --port 8765
python tools/prep_recordings.py recordings/raw recordings/wav   # see docs/recording-script.md
python tools/capture_corpus.py recordings/wav captures --repeats 5
python tools/wire_report.py captures
```

Correlation semantics are in `docs/wire-spec.md`, the failure modes in
`docs/wire-known-issues.md`, and the recording protocol in `docs/recording-script.md`.
The harness targets a URL, so pointing it at any other Realtime-compatible server needs no
change.

*Motivation, for context: a French commune is scoping a fully local voice deployment where
sovereignty is the hard constraint, which is what put this stack under measurement.*

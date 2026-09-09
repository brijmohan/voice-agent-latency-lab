# Externally observable per-turn latency in a fully local speech-to-speech pipeline

**Subject:** `huggingface/speech-to-speech` v1.0.0 (`16d7f98`)
**Method:** black-box observation of the OpenAI Realtime protocol, no fork and no patch
**Sample:** 23 recorded human utterances, 5 replays each, 115 turns
**Headline:** TTFA median 1.577s. Endpointing is the one stage that is not externally
observable, and its cost is 0.082s on a fluent utterance against 0.689s on a hesitant one.

---

## 1. Objective

Per-turn latency in voice agents is normally measured from **inside** the framework, by
instrumenting the pipeline and emitting its own timings. That approach attributes cost
precisely, but it has three properties that limit it: it requires a patch, it is specific to
one implementation, and it stops at the process boundary, so it cannot see what the client
actually received.

This work asks a narrower question:

> **How much of a cascaded voice pipeline's latency budget can be recovered from outside the
> process, using only its public protocol, and which parts cannot be recovered at all?**

The answer is intended to be useful in two directions. Where the wire suffices, measurement
becomes portable across implementations and versions without modification. Where it does not,
the gap identifies precisely what internal instrumentation is needed for and why it cannot
be replaced.

**This is not a comparison between systems.** No second stack appears in these results. A
cascaded pipeline calling cloud APIs and a fully local pipeline on a laptop differ in hosting
far more than in architecture, so a shared table would attribute to design what is caused by
deployment. The unit of comparison here is one system against itself.

## 2. Background and terminology

### 2.1 The quantity of interest

The user-perceived latency of a spoken turn is the interval from the moment the speaker stops
talking to the moment the first audio of the reply is audible. Following the convention used
in the LiveKit half of this repository, this is written **TTFA** (time to first audio).

In a cascaded pipeline TTFA decomposes into three sequential costs:

1. deciding that the speaker has finished (**endpointing**) and producing a transcript,
2. generating the reply text (**LLM**),
3. synthesising the first audible fragment of it (**TTS**).

### 2.2 Terms as used here

**Endpointing** is the decision that a speaker's turn has ended. It is distinct from voice
activity detection (VAD), which detects the presence of speech. A pause is not an endpoint,
and the difficulty is entirely in that distinction. The subject system uses Smart Turn v3.2,
an acoustic model producing a completion probability, at a single global threshold of 0.50.

**Turn** is one exchange as the speaker experienced it: they spoke, possibly with pauses, and
received one reply.

**Revision** is a restatement of an in-progress transcript. The subject system emits each
speech segment as a new conversation item whose transcript restates the whole utterance so
far, so one turn commonly produces several.

**Segment** is a contiguous run of detected speech. Segments and revisions are not the same
count: several segments may be folded into one revision.

### 2.3 Prior and related measurement

Instrumentation inside this pipeline exists in `pipeline/turn_latency.py`, proposed in
upstream PR #539, which emits per-turn `stt`, `llm`, `tts_ttfa` and `e2e` from within the
process. The present work is complementary rather than competing, and Section 7 states the
division precisely.

Externally, `livekit/eot-bench` (Apache-2.0, 14 languages) evaluates end-of-turn models
including Smart Turn, and the Full-Duplex-Bench family evaluates conversational dynamics of
full-duplex models. Neither measures the latency decomposition of a deployed cascaded stack,
which is the gap this addresses.

## 3. Measurement model

### 3.1 Grouping

The Realtime protocol provides no key linking a response to the input that caused it:
`response.created` carries no `item_id`. Grouping is therefore temporal. A turn is defined as
every input-side event between one `response.created` and the next, together with that
response's events.

The anchor within a turn is the **last** `input_audio_buffer.speech_stopped` before
`response.created`, on the grounds that the speaker's wait begins when they last stopped
talking. This is not a free choice: anchoring on the first speech stop instead reports 13.015s
for a turn in which the speaker waited 3.334s, and inflates the pooled 99th percentile from
3.192s to 12.947s while producing output that is not obviously wrong.

### 3.2 Quantities

| Symbol | Definition | Status |
|---|---|---|
| `hold` | last `speech_stopped` to last `…transcription.completed` | measured |
| `llm` | last `…transcription.completed` to `response.created` | measured |
| `tts` | `response.created` to first `response.output_audio.delta` | measured |
| `TTFA` | last `speech_stopped` to first `response.output_audio.delta` | measured, equals the sum |
| segments | count of `speech_stopped` in the turn | measured |
| revisions | distinct input `item_id`s with a completed transcript | measured |

Two of these carry names that would mislead if read literally, and both were established by
cross-checking against the server's own logs rather than assumed:

- **`hold` is not transcription time.** It contains the endpointer's deliberate delay. The
  subject system adds 600ms of processing delay when Smart Turn returns "incomplete".
- **`llm` is total generation, not time to first token.** `response.created` is emitted when
  the LLM finishes. In a validation session a 1.930s interval matched a logged
  `MLX-LLM: MLX lock released after holding 1.90s`. **No anchor on the wire yields TTFT for
  this system.**

### 3.3 Clock

All timestamps are taken on arrival at the observing client from a single monotonic clock,
never from timestamps inside event payloads and never from position in a file.

## 4. Data

### 4.1 Corpus design

Synthetic speech was rejected. Smart Turn is an acoustic model scoring prosody, so driving it
with a vocoder tests the vocoder. The difference is measurable: a macOS `say` utterance scored
p=0.736, mid-range and ambiguous, where recorded human speech in this corpus scores a median
of 0.923 when complete and 0.028 when incomplete.

23 utterances were recorded by one speaker in a single sitting, in six blocks chosen to
exercise distinct endpointing conditions:

| Block | n | Construction | Purpose |
|---|---|---|---|
| A | 6 | complete sentences, falling prosody | reference condition |
| B | 4 | deliberately unfinished, level or rising pitch | endpointer must hold |
| C | 3 | one utterance with 600ms internal pauses | forces revisions |
| D | 3 | filled pauses and self-correction | disfluency |
| E | 4 | dates, digit strings, spelled letters, one code-switch | transcription stress |
| F | 3 | one or two words | minimum evidence for the endpointer |

Utterance content is fictional throughout: no real names, addresses, telephone numbers or
client identifiers.

### 4.2 Preprocessing

Each take was converted to 16 kHz mono PCM16, normalised to an identical 300ms lead, and had
trailing silence removed. The last step is not cosmetic. Trailing silence enters the
endpointing measurement directly, so takes that happened to end with different pauses would
produce differences originating in the speaker rather than the system. The capture client
appends a fixed silence tail instead, so every utterance is presented under identical
conditions.

### 4.3 Replay protocol

Each utterance was streamed at wall-clock pace into a fresh session, one session per turn,
because a reused session accumulates conversation history and the LLM stage would grow across
the run. Silence continued to stream for the whole wait, as on a live call: an endpointer
that stops receiving audio is not the endpointer under test.

### 4.4 Units of analysis, and what n means

**There are 115 turns but only 23 independent stimuli.** The five replays of an utterance are
repeated measurements of the same input, not independent samples of speech. Any statistic
pooled over 115 therefore has an effective sample size closer to 23 for inference about
speech in general, and closer to 115 only for inference about the system's own repeatability.
Section 6.4 separates the two.

## 5. Setup

| | |
|---|---|
| Subject | `huggingface/speech-to-speech` v1.0.0, commit `16d7f98` |
| Invocation | `speech-to-speech serve --mac-optimal-settings --host 127.0.0.1 --port 8765` |
| STT | Parakeet TDT (mlx) |
| LLM | Qwen3-4B-Instruct-2507, 4-bit (mlx) |
| TTS | Qwen3-TTS-12Hz-1.7B-CustomVoice, 6-bit (mlx) |
| Endpointer | Smart Turn v3.2, ONNX CPU, threshold 0.50 (default) |
| Hardware | Apple M3 Pro, 18 GB, fully local, no network in the inference path |
| Transport | WebSocket to `/v1/realtime`, loopback |
| Observer | external asyncio client, 20ms audio chunks, monotonic arrival timestamps |
| Date | 8 September 2026 |

The observer uses the subject's own `build_session_update`, so the session handshake is the
project's rather than a reimplementation of it.

## 6. Results

### 6.1 Aggregate

115 of 115 turns produced a reply. No silent, held or unclassified turns occurred.

| Stage | P50 | P90 | P99 | max |
|---|---|---|---|---|
| `hold` | 0.098 | 0.723 | 0.985 | 1.089 |
| `llm` | 1.285 | 1.743 | 2.167 | 2.197 |
| `tts` | 0.156 | 0.174 | 0.180 | 0.204 |
| **TTFA** | **1.577** | **2.298** | **3.192** | **3.334** |

The median of the 23 per-utterance medians is 1.487s, against a pooled median of 1.577s. The
pooled percentiles are reported for continuity with the sibling measurements in this
repository, but see Section 6.4 before using the tail.

`tts` is notable for its stability: the interquartile behaviour is flat and the 99th
percentile sits 0.024s above the median.

### 6.2 Endpointing cost by construction

| Block | Median `hold` | Median TTFA | Median segments |
|---|---|---|---|
| A, complete sentences | 0.082 | 1.397 | 2 |
| B, deliberately unfinished | **0.689** | 2.193 | 1 |
| C, internal pauses | 0.400 | 1.579 | 3 |
| D, disfluent | 0.096 | 1.510 | 3 |
| E, transcription stress | 0.100 | 1.652 | 4 |
| F, one or two words | 0.044 | 1.334 | 1 |

The B against A difference of 0.607s is the cost of the endpointer correctly declining to
treat an unfinished sentence as a turn. It did not truncate the speaker: it waited, and
proceeded only when they did not resume.

### 6.3 Endpointer decision behaviour

Smart Turn verdicts are **not** present on the wire and were recovered from server logs,
scoped to the sweep window.

| Verdict | n | Share | Median p | Consequence in the pipeline |
|---|---|---|---|---|
| complete | 140 | 51% | 0.923 | 800ms speculative reopen grace |
| incomplete | 135 | 49% | 0.028 | 2000ms grace, processing delayed 600ms |

275 decisions over 115 turns is 2.39 per turn, matching the 2.39 mean speech segments per
turn measured independently on the wire. The two medians, 0.923 and 0.028 against a 0.50
threshold, indicate the model is decisive rather than marginal on this speaker.

**Approximately half of all endpointing decisions are holds, and none of them appear in any
TTFA average.**

### 6.4 Repeatability, and the limits of the pooled tail

Decomposing TTFA variation into within-utterance (repeated measurement of one input) and
between-utterance (different inputs):

| Component | SD |
|---|---|
| Within-utterance, system noise | 0.217s |
| Between-utterance, content variation | 0.430s |

**Variation is dominated 2:1 by what was said rather than by the system.** The median
within-utterance range across five replays is 0.134s.

The practical consequence: the pooled 99th percentile of 3.192s is substantially a statement
about which sentence was uttered, not about a system tail. It is a valid regression signal
for a fixed corpus and an invalid estimate of what an arbitrary caller would experience.

### 6.5 Per-utterance detail

| Utt | Segs | Revs | Median `hold` | Median TTFA | Min | Max | Range |
|---|---|---|---|---|---|---|---|
| A01 | 1 | 1 | 0.109 | 1.453 | 1.419 | 2.054 | 0.634 |
| A02 | 2 | 1 | 0.056 | 1.351 | 1.337 | 1.385 | 0.048 |
| A03 | 1 | 1 | 0.072 | 1.294 | 1.267 | 1.354 | 0.087 |
| A04 | 1 | 1 | 0.083 | 1.615 | 1.586 | 1.944 | 0.358 |
| A05 | 2 | 2 | 0.082 | 1.444 | 1.410 | 2.329 | 0.920 |
| A06 | 2 | 2 | 0.076 | 1.228 | 1.211 | 1.258 | 0.047 |
| B01 | 1 | 1 | 0.681 | 2.179 | 2.166 | 2.191 | 0.025 |
| B02 | 1 | 1 | 0.707 | 2.198 | 2.178 | 2.523 | 0.346 |
| B03 | 1 | 1 | 0.704 | 2.194 | 2.184 | 2.229 | 0.046 |
| B04 | 2 | 1 | 0.689 | 2.195 | 2.182 | 2.204 | 0.022 |
| C01 | 2 | 2 | 0.065 | 1.214 | 1.194 | 1.579 | 0.385 |
| C02 | 3 | 3 | 0.730 | 2.299 | 2.266 | 2.371 | 0.105 |
| C03 | 4 | 3 | 0.141 | 1.578 | 1.467 | 2.730 | 1.263 |
| D01 | 2 | 1 | 0.087 | 1.487 | 1.380 | 1.740 | 0.360 |
| D02 | 3 | 2 | 0.076 | 1.411 | 1.376 | 1.510 | 0.134 |
| D03 | 4 | 2 | 0.725 | 2.182 | 2.169 | 2.221 | 0.052 |
| E01 | 2 | 2 | 0.078 | 1.462 | 1.370 | 2.142 | 0.772 |
| E02 | 5 | 3 | 0.117 | 1.582 | 1.554 | 1.674 | 0.120 |
| E03 | 11 | 4 | 0.472 | 2.791 | 2.496 | 3.334 | 0.838 |
| E04 | 2 | 1 | 0.068 | 1.630 | 1.549 | 2.018 | 0.470 |
| F01 | 1 | 1 | 0.044 | 1.311 | 1.289 | 1.846 | 0.558 |
| F02 | 1 | 1 | 0.044 | 1.360 | 1.331 | 1.369 | 0.038 |
| F03 | 1 | 1 | 0.044 | 1.332 | 1.321 | 1.338 | 0.017 |

### 6.6 Turn structure

61% of turns (70 of 115) comprised more than one speech segment. Mean 2.39 segments and 1.63
revisions per turn, maximum 11 segments folded into 4 revisions for the spelled-out address in
E03. Multi-segment turns are therefore the ordinary case in this corpus, not an edge case,
which is why the anchoring choice in Section 3.1 dominates the result.

Each superseded revision represents LLM work already performed. Server logs recorded 192
MLX-LLM lock acquisitions across 115 turns. **This figure is reported tentatively**: the same
log recorded only 21 Parakeet acquisitions over the same window, which is not consistent with
any explanation currently available, so the logging itself is not fully understood.

## 7. What is not externally observable, and why

`input_audio_buffer.speech_stopped` is emitted **after** the endpointing decision has already
been taken. In a validated session the server's logged Smart Turn completion and the wire's
`speech_stopped` coincided to within one millisecond.

Consequently:

- The wire yields three of the four stages exactly.
- The wire yields **no** view of endpointing as a separable cost, only its consequence folded
  into `hold`.
- The wire yields **no** time to first token, since `response.created` marks completion.
- Internal instrumentation yields endpointing attribution and the completion probability, and
  cannot observe what the client received.

The two are complementary and neither is sufficient. External observation additionally
requires no patch, so it applies unchanged to any released version and to any other server
implementing the same protocol.

## 8. Threats to validity

1. **Single speaker, single language, single machine, single configuration.** No claim
   generalises beyond this speaker. Nothing here says anything about French.
2. **Replay measures system variance, not population variance** (Section 6.4).
3. **Four outcome classes never occurred.** No silent turn, held turn, agent-initiated turn or
   tool turn arose in 115 captures, because the corpus was scripted and the session had no
   tools. Those code paths rest on synthetic tests alone.
4. **Barge-in was never exercised.** Interrupting speech is assigned to the following turn by
   construction, which is reasoned and unmeasured.
5. **Tool turns are known to be mis-grouped.** One user turn that invokes a tool produces
   several responses and would be split. This is recorded as a failing test rather than fixed.
6. **The 800ms and 2000ms grace windows** appear in logs and their implementation has not been
   read, so any account of endpointing here is behavioural rather than mechanistic.
7. **Server-log-derived figures** (Sections 6.3, 6.6) are outside the protocol and depend on
   log semantics, one of which is demonstrably not understood.
8. **Cold start.** TTS real-time factor was 2.25 in steady state against 0.87 during warm-up,
   so early turns in an unwarmed process would not resemble these.

## 9. Conclusions

1. **Most of a cascaded latency budget is externally recoverable.** Three of four stages are
   obtained exactly from the public protocol, with no patch and no fork, on a released version.
2. **Endpointing is the exception and is structurally invisible from outside**, because the
   protocol reports the consequence of the decision rather than the decision.
3. **The endpointer's caution is a first-order latency term.** Half of all decisions were
   holds, each costing roughly 600ms, and the effect is entirely absent from aggregate TTFA.
   A system tuned only on mean latency is tuning against a number that cannot see this.
4. **Turn structure, not the model, drives the tail on this corpus.** Content variation exceeds
   system noise 2:1, and the multi-segment structure of ordinary speech determines whether a
   measurement is correct at all.
5. **Recorded and replayed human speech makes the measurement both reproducible and
   acoustically valid**, which synthetic speech cannot be for an acoustic endpointer.

## 10. Future work

1. **Per-language endpointing thresholds.** Smart Turn v3.2 applies one global threshold with
   no language conditioning while the default STT declares 25 languages. Published prior art
   indicates monolingual voice-activity-projection models do not transfer across languages.
   The experiment is a threshold sweep reported as cutoff rate against added latency, as a
   curve rather than a single operating point.
2. **A parallel French corpus** with matched utterance functions, making the pair a controlled
   bilingual comparison rather than two unrelated runs.
3. **Reading the speculative reopen grace path**, converting Section 6.3 from behavioural
   observation into mechanism.
4. **External reference benchmarks.** These measurements are currently compared only against
   themselves. Full-Duplex-Bench v1.5 and HumDial are the reference points the field
   cross-references.
5. **Appropriate rather than minimal timing.** Human conversational gaps cluster near 200ms,
   and delays beyond roughly 700ms are read as signalling a dispreferred response. If timing
   carries pragmatic meaning, the objective is a target offset distribution conditioned on the
   act being performed, not a minimum. That reframes what a latency budget is for.

## 11. Reproduction

```bash
speech-to-speech serve --mac-optimal-settings --host 127.0.0.1 --port 8765
python tools/prep_recordings.py recordings/raw recordings/wav
python tools/capture_corpus.py recordings/wav captures --repeats 5
python tools/wire_report.py captures
```

The recording protocol is in `docs/recording-script.md`, correlation semantics in
`docs/wire-spec.md`, known failure modes in `docs/wire-known-issues.md`, and test coverage in
`docs/TESTING.md`. Five real capture sessions are committed as fixtures so the correlator's
behaviour can be checked without the 19 MB corpus. The observer targets a URL, so applying it
to another Realtime-compatible server requires no change.

*Context: this measurement was undertaken while scoping a fully local voice deployment for a
French commune, where sovereignty is a hard constraint.*

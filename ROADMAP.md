# Roadmap

The method is one latency decomposition applied to voice agent stacks that do not share an
event model. Per-turn instrumentation exists inside individual stacks. What does not exist
is a decomposition that stays comparable *across* them, because each framework measures
whatever it happens to emit.

The claim under test is that

```
TTFA = end_of_utterance + llm_ttft + tts_ttfb
```

is stack-independent, and that the interesting part is the honest account of where each
stack makes it hard to compute.

Milestones after v0.1 are driven by a French municipal deployment currently being scoped,
where the operating constraints are fully local inference, no data leaving the building, and
callers who skew elderly and hesitant. Those constraints change which numbers matter, and
they are the reason for the ordering below.

---

## v0.1  Shipped

LiveKit Agents, one cascaded pipeline, measured and reported at the tail.

- [x] Turn correlator joining `EOUMetrics` / `LLMMetrics` / `TTSMetrics` on `speech_id`,
      with correctness independent of arrival order.
- [x] Every `speech_id` classified rather than only the ones that fit the table: responded,
      silent, agent-initiated, speculative, incomplete.
- [x] Retry separated from slowness where the evidence supports it.
- [x] `CORRECTIONS.md`, recording claims made here that turned out to be wrong.
- [x] A stated list of what this does not measure.

## v0.2  A second stack  (Shipped 9 Sep 2026)

`huggingface/speech-to-speech` v1.0.0, measured from **outside the process** over the
Realtime API it already exposes. Results in [REPORT-speech-to-speech.md](REPORT-speech-to-speech.md).

- [x] Adapter that takes a running stack as a target rather than vendoring it. No fork, no
      patch, no submodule. The harness connects to a URL.
- [x] Upstream commit pinned (`16d7f98`, v1.0.0) so a number can be reproduced.
- [x] A recorded human corpus, replayed, so n = 115 and P99 is reportable for the first time.
- [ ] ~~Publish both decompositions side by side.~~ **Deliberately not done.**

**Why the side-by-side was dropped.** The LiveKit half runs three cloud APIs from Lille; the
second stack runs fully local on a laptop. A shared table would be read as a statement about
architecture when it is dominated by cloud versus local. The method survives that; a league
table does not, and the moment this reads as "my benchmark says X is faster" the methodology
is worth nothing.

What replaced it is better: the two halves measure from **different vantage points**, one
inside the framework's event stream and one on the wire, and the finding is what each can and
cannot see. Endpointing is invisible from outside. Transport is invisible from inside.

**Acceptance, met:** the same four stages are reported for both stacks, and every stage that
cannot be measured on one of them is named as unmeasured rather than estimated.

## v0.3  Does one global endpointing threshold work in more than one language?

`speech-to-speech` applies Smart Turn v3.2 with a single global threshold and no language or
speaker conditioning, while its default STT declares 25 languages. LiveKit ships per-language
thresholds for the same task, and `livekit/eot-bench` (Apache-2.0, 14 languages) evaluates
Smart Turn among others. Published work on voice activity projection reports that monolingual
models do not transfer across languages.

Everything measured in this repository so far is English. The one French phrase that appeared
in the corpus, *salle des mariages*, came back as *salle de mariage*. That is a warning, not
a result.

### v0.3a  A parallel French corpus, self-recorded

The cheap half, and it comes first because it is not blocked on anything.

- [ ] Record the same 23 utterance functions in French, same speaker, same microphone,
      **same block IDs**, so each French utterance pairs with its English counterpart.
- [ ] Report hold rate, hold cost and TTFA per block, per language.
- [ ] Report the Smart Turn probability distribution per language. The English corpus gives
      0.923 median when complete and 0.028 when incomplete, decisively separated. **Whether
      that separation survives in French is the whole question.**

**Why self-recorded beats real calls for this specific question.** Matched utterance
functions control for content, so a difference between the two runs is attributable to
language rather than to what was said. Real calls cannot offer that, and would confound the
one variable being tested.

**Acceptance:** a per-language table on identical utterance functions, and a stated answer to
whether a single threshold is defensible across the two.

### v0.3b  Real consented calls

The deployment-grade half, gated on consent and a completed DPIA, answering a different
question: what real callers do, as opposed to what one speaker can produce on request.

- [ ] Annotate true end-of-turn points on a consented set of French municipal calls.
- [ ] Sweep the threshold and report **cutoff rate against added latency as a curve**, not a
      single operating point.
- [ ] Report per caller profile, since the question is whether one global number can serve a
      heterogeneous population.

**On the data.** Real call audio never leaves the deployment and is never published. What
gets published is the annotation protocol, the schema, and a synthetic set generated with
TTS. A method that only works if you hold the private audio is not a contribution.

## v0.4  Latency as a regression gate

- [x] CI on every push: ruff, the full suite on 3.12 and 3.13, and a guard that the lean
      import path stays free of `livekit` and `speech_to_speech`.
- [x] Releases gated on those tests, published to PyPI by Trusted Publishing so no API token
      exists anywhere.
- [ ] Run the harness in CI over the committed fixtures.
- [ ] Fail the build when P50 TTFA regresses past a stated budget.

A latency budget that is written down but not enforced is a comment. See
[docs/TESTING.md](docs/TESTING.md) for what the suite covers today and what it does not.

## v0.5  Measured against something other than itself

This repo currently benchmarks nothing but itself, which is a real limit on what any number
here means.

- [ ] Full-Duplex-Bench v1.5 (arXiv 2507.23159) as an external reference.
- [ ] HumDial (ICASSP 2026), which is real human dual-channel conversation rather than
      synthesised dialogue.

Both are what the field actually cross-references. Adopting them is how these measurements
stop being self-referential.

---

## Deliberately not on this roadmap

- **Kubernetes and Terraform.** Dropped in September 2026. Nothing here needs them and
  advertising work that is not being done is worse than a short list.
- **Anything at scale.** Every number in this repo came from one laptop. That is stated in
  the README and it is not going to be quietly upgraded.
- **A verdict on which stack is better.** See v0.2.

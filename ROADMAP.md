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

## v0.2  A second stack

Apply the same decomposition to `huggingface/speech-to-speech`, which has a different event
model and no shared turn key.

- [ ] Adapter that takes a running stack as a target rather than vendoring it. The harness
      must not own the systems it measures, so no submodule and no fork dependency.
- [ ] Pin the upstream commit in the README so a reported number can be reproduced.
- [ ] Publish both decompositions side by side.

**Not a league table.** The contribution is a method. The moment this reads as "my benchmark
says stack X is faster", the methodology is worth nothing and the numbers get argued about
instead of used.

**Acceptance:** the same four stages are reported for both stacks, and every stage that
cannot be measured on one of them is named as unmeasured rather than estimated.

## v0.3  Calibration for a real deployment

`speech-to-speech` applies Smart Turn v3.2 with a single global threshold and no language or
speaker conditioning, while its default STT declares 25 languages. LiveKit ships per-language
thresholds for the same task, and `livekit/eot-bench` (Apache-2.0, 14 languages) evaluates
Smart Turn among others, so the concern has public prior art.

A caller who pauses mid-sentence gets cut off. In a municipal front office that is most of
the callers.

- [ ] Annotate true end-of-turn points on a consented set of French municipal calls.
- [ ] Sweep the threshold and report **cutoff rate against added latency as a curve**, not a
      single operating point. Both halves are real costs and they trade against each other.
- [ ] Report the result per caller profile, since the whole question is whether one global
      number can serve a heterogeneous population.
- [ ] Send anything that generalises upstream as a PR.

**Acceptance:** a stated operating point with the cost of choosing it, and a procedure
someone else can run on their own callers.

**On the data.** Real call audio never leaves the deployment and is never published. What
gets published is the annotation protocol, the schema, and a synthetic set generated with
TTS. A method that only works if you have the private audio is not a contribution.

## v0.4  Latency as a regression gate

- [ ] Run the harness in CI over the synthetic set.
- [ ] Fail the build when P50 TTFA regresses past a stated budget.

A latency budget that is written down but not enforced is a comment.

---

## Deliberately not on this roadmap

- **Kubernetes and Terraform.** Dropped in September 2026. Nothing here needs them and
  advertising work that is not being done is worse than a short list.
- **Anything at scale.** Every number in this repo came from one laptop. That is stated in
  the README and it is not going to be quietly upgraded.
- **A verdict on which stack is better.** See v0.2.

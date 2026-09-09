# Testing

What is tested, at which layer, and what is not. The last part is the point: a repo whose
subject is measurement should be precise about the confidence its own tests earn.

```
uv run pytest tests/ -q                                    # 51 passed, 1 skipped, 1 xfailed
WIRE_CAPTURES=path/to/captures uv run pytest tests/ -q     # 52 passed, 1 xfailed
```

## The layers

| Layer | Where | n | Covers |
|---|---|---|---|
| Unit, LiveKit correlator | `tests/test_correlator.py` | 24 | join on `speech_id`, arrival order, outcome classification, retry inference |
| Unit, wire correlator | `tests/test_wire_correlator.py` | 13 | response-boundary grouping, anchors, outcomes |
| Unit, audio prep | `tests/test_prep_recordings.py` | 6 | speech-boundary detection at and around the threshold |
| Unit, metric sink | `tests/test_sink.py` | 5 | round trip, flush, append, unmodelled types |
| Invariant | `tests/test_wire_correlator.py` | 5 | properties asserted over *every* capture available, not just named ones |
| Regression fixture | `tests/fixtures/wire/` | 5 sessions | real captures, byte-identical results, 88 KB |
| Corpus | opt-in, `WIRE_CAPTURES` | 115 | the full 23 x 5 sweep, 19 MB, not in git |
| Manual / user | `SMOKE-TEST.md` | 2 runs | a human speaking into a real microphone |

## Why the fixtures are trimmed

The five committed sessions are real, with the base64 audio payloads blanked. Every field the
correlator reads is untouched, and the result is byte-identical: 947 KB becomes 88 KB. A
fresh clone can therefore run the regression that matters (E03 reporting 3.334s rather than
13.015s) without a 19 MB download.

## Why invariants and not just examples

Example tests check the cases you thought of. The invariants check the ones you did not:
no stage is ever negative, anchors are monotonic, stages sum to TTFA, segments are never
fewer than revisions, and a responded turn has at least one transcript. A negative stage
would not raise. It would quietly pull the mean down, which is exactly the shape of bug that
makes a latency table look good.

## What is deliberately not covered

**Integration against a live server.** `capture_corpus.py` is exercised only by hand. Testing
it needs `speech-to-speech serve` running with several GB of models loaded, which does not
belong in a unit suite. It is covered by the manual protocol in `SMOKE-TEST.md` instead, and
that protocol has caught two real bugs that unit tests did not: console mode swallowing
stdout, and the pipeline-slot release race.

**Four outcome paths have never seen real data.** `SILENT`, `HELD`, `AGENT_INITIATED` and
tool turns had zero occurrences in 115 captures. They are covered by synthetic tests, which
proves they are representable and proves nothing about how they behave in the wild. See
`docs/wire-known-issues.md`.

**Barge-in.** Zero occurrences. The grouping assigns interrupting speech to the next turn,
which is reasoned rather than measured.

**CI.** Nothing runs on push yet. `ROADMAP.md` v0.4 covers the latency regression gate, and
until that exists these tests only run when someone remembers.

**Public benchmarks.** Not attempted. Full-Duplex-Bench v1.5 (arXiv 2507.23159) and HumDial
(ICASSP 2026) are the two the field actually cross-references, and adopting them would let
these measurements be compared against something other than themselves. That is roadmap, not
a claim, and this repo currently benchmarks nothing but itself.

## The bug this suite exists because of

The first read of the corpus anchored TTFA on the *first* `speech_stopped` rather than the
last. Every number was plausible. The pooled P99 read 12.947s instead of 3.192s, a factor of
four, and nothing about the output looked wrong. `test_multi_segment_turn_anchors_on_the_last_speech_stop`
is that bug, pinned to a real session, so it cannot come back quietly.

# Wire correlator: behaviour to build
*Decision taken 8 Sep 2026: response-boundary grouping. Rationale in `CORRELATOR-BRIEF.md`.*

You write it. The acceptance tests in `tests/test_wire_correlator.py` are already written and
currently fail. Make them pass.

## Input

The JSONL that `capture_corpus.py` writes. Line 1 is `_capture.meta`; every other line is

```json
{"t": 3.351, "type": "response.created", "event": { ...raw protocol event... }}
```

`t` is seconds from connection open, monotonic, stamped on arrival. **`t` is the
measurement.** Never use list position as a proxy for it.

## Grouping rule

A **turn** is every input-side event between the previous `response.done` (or the start of
the session) and the next `response.created`, together with that response's own events.

This is forced rather than chosen: `response.created` carries no `item_id`, so there is no
key to join a response to the input that caused it. Grouping has to be temporal.

## Per-turn quantities

| Name | Definition |
|---|---|
| `speech_stopped_t` | the **last** `input_audio_buffer.speech_stopped` before `response.created` |
| `transcript_completed_t` | the **last** `…input_audio_transcription.completed` before `response.created` |
| `response_created_t` | `response.created` |
| `first_audio_t` | the **first** `response.output_audio.delta` |
| `hold` | `transcript_completed_t - speech_stopped_t` |
| `llm` | `response_created_t - transcript_completed_t` |
| `tts` | `first_audio_t - response_created_t` |
| `ttfa` | `first_audio_t - speech_stopped_t` |
| `segments` | count of `speech_stopped` in the turn |
| `revisions` | count of **distinct** input `item_id`s with a completed transcription |
| `final_transcript` | transcript of the last completed transcription |

`hold` is **not** transcription time. It contains the endpointer's deliberate delay: Smart
Turn adds 600ms when it judges an utterance incomplete. Naming it `transcription_delay`
would file endpointing cost under a transcription label.

`llm` is total generation, **not** time to first token. `response.created` is emitted when
the LLM finishes. The wire cannot give you TTFT on this stack.

## Outcomes

Every turn gets one, and every turn is counted. Excluding without counting is the failure
the first correlator was written to prevent.

- `RESPONDED` — the caller spoke and heard a reply. **Guaranteed to have a defined `ttfa`.**
- `AGENT_INITIATED` — a response with no caller speech in front of it. It answers nothing, so
  `ttfa` is undefined by construction. This outcome exists so that `RESPONDED` can carry a
  guarantee instead of a label.
- `SILENT` — a response was created and produced no `output_audio.delta`.
- `HELD` — user speech, and the capture ended with no `response.created`. The capture writes
  a `_capture.timeout` marker. `ttfa` is undefined; the useful quantity is how long it held.

Zero `SILENT`, `HELD` and `AGENT_INITIATED` occur in the current 115 captures. All must still be
representable. A correlator that cannot express an outcome will silently drop it, and the
first time it happens will be the run that mattered.

## Known limitation to write down, not solve now

One user turn can produce more than one response when tools are involved, and this grouping
will split it. Zero occurrences in this corpus because the session had no tools. PR #539 hit
exactly this and logged two records under one key. Record it as a limitation with a test
marked xfail rather than pretending it cannot happen.

## Suggested surface

Change it if you prefer; the tests follow your names, not the other way round.

```python
correlate(events: list[dict]) -> list[Turn]
```

with `Turn` exposing the quantities above and `outcome`.

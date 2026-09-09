# Known issues, before any of this is published
*8 Sep 2026. `wire_correlator.py` passes 10 tests plus 1 xfail over 115 real captures.*

Ordered by what would embarrass you first.

## Correctness, untested paths

1. **Tool turns split into two.** One user turn that calls a tool produces two responses, and
   response-boundary grouping makes that two turns. Recorded as an xfail. **Zero occurrences
   in the corpus because that session had no tools**, so it is untested rather than absent.
   PR #539 hit exactly this and logged two records under one key.

2. ~~A response with no preceding speech is emitted as RESPONDED with `ttfa=None`.~~
   **Fixed 9 Sep.** Such a turn is now `AGENT_INITIATED`, and a test asserts over the whole
   corpus that every RESPONDED turn has a defined TTFA. The outcome now carries a guarantee
   rather than a label.

3. **Barge-in is reasoned, not measured.** Speech arriving while a response is playing is
   assigned to the *next* turn. Zero occurrences in 115 captures. The alternative reading,
   grouping from `response.done`, would silently discard it entirely, which is why it works
   this way, but no real barge-in has ever been through this code.

4. **HELD, SILENT and AGENT_INITIATED are covered by synthetic tests only.** None occurred in 115 captures.
   They are representable, which is the point, but the code paths have never seen real data.

5. **Events with identical `t` are ordered by file position.** Python's sort is stable, so a
   tie falls back to arrival order. Verified to produce `hold=0.0` rather than a negative
   number, so it degrades safely, but it is an unstated assumption.

6. **`response.created` arriving while another is open** emits the open one defensively.
   No test covers it.

## Measurement caveats that must travel with any number

7. **`llm` is total generation, not time to first token.** `response.created` fires when the
   LLM finishes. **The wire cannot give TTFT on this stack at any anchor.** Reporting it as
   TTFT would be a fabrication.

8. **`hold` contains the endpointer's deliberate delay**, not just transcription. Smart Turn
   adds 600ms on an "incomplete" verdict. Do not rename it `transcription_delay`.

9. **The pooled P99 partly measures which sentence was picked.** 23 utterances, 5 replays.
   Per-utterance spread is the honest system-variance figure (median range 0.134s). The
   pooled tail is not a population tail and must never be presented as one.

10. **One speaker, one language, one session, one machine, one configuration.** No claim
    about anyone else's voice, and no claim about French.

11. **Replay gives system variance, not population variance.** Correct for tuning and
    regression gates. Wrong for "what will callers experience".

## Unexplained, do not build on

12. **Parakeet logged 21 MLX lock acquisitions across 115 turns.** That fits no story I can
    defend. Until it is understood, the companion figures (192 LLM acquisitions, 121 TTS)
    should not be quoted either.

13. **The 800ms / 2000ms "speculative reopen grace"** appears in the VAD log and the source
    has not been read. It sits in the endpointing path, so any endpointing claim depends on
    understanding it.

14. **The ~2.501s ceiling in the LiveKit half** (lab01) is still unexplained and is not
    `max_delay`, which defaults to 3.0.

## Repo hygiene

15. **23M of audio and captures**: `captures/` 19M, `recordings/wav/` 3.1M, `recordings/raw/`
    1.2M. Now gitignored. The corpus is reproducible from `docs/recording-script.md`; the audio is
    not the artifact and Brij's recorded voice does not belong in a public repo by default.

16. **No README.** A reader landing on this directory has no entry point.

17. **No CLI.** `wire_correlator.py` is importable and there is no way to run it over a
    directory and get a table without writing a script.

18. **Not yet in the public repo.** Moving it into `voice-agent-latency-lab` as v0.2 is a
    separate decision, and `ROADMAP.md` there currently promises a comparison this work has
    deliberately not produced.

## The thing that is not an issue but reads like one

The corpus produced **zero silent turns, zero held turns, zero agent-initiated turns, zero
barge-ins and zero tool turns**. That is not the harness being incomplete. It is a clean corpus recorded to a script,
and it means four outcome paths are carried on synthetic tests alone. Say so rather than
implying 115 captures exercised everything.

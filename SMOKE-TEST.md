# Manual smoke test

The correlator classifies every `speech_id` into an outcome, and most of those outcomes
cannot be produced on demand by a unit test because they depend on how a human actually
talks. This is the protocol for producing them deliberately.

**Two runs, not one.** A session in which you interrupt the agent and talk over pauses does
not produce a latency distribution comparable to a clean booking conversation. Mixing them
gives a table that describes neither.

| Run | Purpose | What it produces |
|---|---|---|
| **A. Clean** | the published numbers | the percentile table, `n` stated |
| **B. Adversarial** | the classification check | agent-initiated, speculative, interrupted |

Run A first, while your voice is fresh and before you have started thinking about edge
cases. The table comes from A. Only B's ledger matters, not B's timings.

---

## Before you start

```bash
cd /Users/brij/PROJECTS/voice-agent-latency-lab
uv sync
uv run pytest -q                     # 23 passed
```

Quiet room, same microphone both runs, same distance. Changing the input between runs
changes `transcription_delay`, which is the least stable stage in the budget.

Launch:

```bash
LOG=data/logs/$(date +%Y%m%d-%H%M%S)-runA-v1.8.0.log
script -q "$LOG" .venv/bin/python src/apresvous/agent.py console
```

`script` rather than `tee`, because console mode needs a real TTY. End with **Ctrl+C**,
which is what fires the shutdown callback that prints the ledger. Killing the terminal
window instead loses it.

**Console mode owns stdout and swallows anything an event handler prints**, so you will see
no metrics scroll past during the run. That is expected. Raw fragments go to
`data/metrics/<timestamp>.jsonl`, written and flushed per fragment, and the path is echoed
at shutdown. **That JSONL file is the artifact to keep**, not the terminal capture.

---

## Run A: clean booking conversation

Speak normally. Finish each sentence with falling prosody and then stop. Do not correct
yourself, do not trail off, do not interrupt. The agent is Robin, booking a civil ceremony
at a fictional city hall called Northgate.

| # | Say | Why this line exists |
|---|---|---|
| 0 | *(say nothing, let the greeting play)* | the agent-initiated turn. No preceding end of utterance, so no TTFA. |
| 1 | "I'd like to book a ceremony slot please." | first real turn, warm start, ignore its timing |
| 2 | "The first name is Brij Mohan Srivastava." | one clean utterance, no mid-name pause |
| 3 | "The second name is Claire Dubois." | second party |
| 4 | "What dates do you have?" | forces the agent to read the slot list, which is a long reply and usually more than one TTS segment |
| 5 | "Saturday the fourteenth of June at ten in the morning." | commits a slot |
| 6 | "The email is brij at nijta dot com." | one clean utterance, no spelling out |
| 7 | "Yes that's right." | short turn, tests the fast end of the distribution |
| 8 | "Actually, can we do the afternoon instead?" | a change of mind, longer reasoning reply |
| 9 | "Yes, two in the afternoon." | |
| 10 | "Can you read the details back to me?" | longest reply of the session, several TTS segments |
| 11 | "That's all, thank you." | |

Then **Ctrl+C**.

**Target: at least 10 responded turns.** If the ledger shows fewer, run it again rather
than publishing a table with a smaller `n` than the one it replaces.

### What Run A should show

```
[correlator] N speech_id(s) seen:
[correlator]   responded        >= 10
[correlator]   agent_initiated  1
[correlator]   speculative      0 or a few
[correlator]   incomplete       0
```

`incomplete` above 0 in a clean run means something is unclassified and needs looking at
before anything is published.

---

## Run B: adversarial

New log file. This run exists to make the classifier produce every outcome. Its timings are
discarded.

```bash
LOG=data/logs/$(date +%Y%m%d-%H%M%S)-runB-v1.8.0.log
script -q "$LOG" .venv/bin/python src/apresvous/agent.py console
```

| # | Do this | Targets |
|---|---|---|
| 0 | say nothing, let the greeting play | `agent_initiated` |
| 1 | "I want to book... a ceremony... for two people." Leave about half a second between each chunk, and keep your pitch **up** at each gap so it sounds unfinished. | `speculative`. Each chunk finalises a transcript and fires a preemptive generation; only the last commits, so the earlier ones are LLM-only orphans. |
| 2 | "The names are Brij... Mohan... Srivastava... and Claire... Dubois." Same trick, more chunks. | more `speculative` |
| 3 | "The email is b, r, i, j, at... n, i, j, t, a... dot com." Spell it, pausing between groups. | `speculative`, and a genuinely hard transcript |
| 4 | Ask "what dates do you have?", then **interrupt about half a second into the reply** with "no wait, sorry." | interruption path. May produce a `silent` turn or an interrupted assistant message. |
| 5 | Interrupt again, this time **within the first word** of a reply. | best chance at `silent`: the caller committed a turn and never heard audio |
| 6 | Say "Wait. Wait. Wait. Wait. Wait." then stop. Then, separately, say "Wait. Wait. Wait. Wait." | the prosody probe. Near-identical utterances, historically opposite end-of-turn decisions (0.915 against 0.068). Watch `end_of_utterance_delay` on the two. |
| 7 | Stay completely silent for about ten seconds. | nothing should be emitted. Silence is not a turn. |

Then **Ctrl+C**.

### Honest limits of Run B

- **`silent` is reproducible, but only by step 5.** Interrupting *inside the first word* of
  a reply produced one on 7 Sep: end-of-utterance 2.501s, `ttft=0.577s`, `cancelled=False`,
  no audio. Interrupting half a second in (step 4) does not do it, because the first audio
  frame has already gone out. If you get none, you were too slow, not unlucky.
- **`retried` cannot be provoked.** It needs a provider 504. It is verified by replay
  instead, below.
- **`llm_calls` above 1 on a single turn needs tool steps**, and this agent has no tools.
  Expect 1 per responded turn here. The multi-step path is covered by unit test.

---

## After both runs

```bash
# Re-derive Run A's table from its capture. Must match what the live ledger printed.
uv run python tools/replay_log.py data/metrics/<runA>.jsonl --llm-timeout 10.0

# Order independence, against real data rather than synthetic fragments.
uv run python tools/replay_log.py data/metrics/<runA>.jsonl --scramble
```

`--scramble` must produce the **same turns with the same TTFA values** in some order. If
scrambling changes a number, the join is positional somewhere and the whole method is void.

Retry detection, against the 24 Aug log which contains a real provider 504:

```bash
uv run python tools/replay_log.py \
  ../VoiceAgent/labs/lab01-first-agent/data/logs/20260824-154309-M2.log --llm-timeout 10.0
# expect: retried: speech_80b45ef1d551 (ttft 16.350s exceeds per-attempt timeout 10.0s)
```

Note that replaying an old log reports the greeting as `incomplete` rather than
`agent_initiated`. That is correct: those logs predate the `speech_created` stream, and
without it an `LLM + TTS` shape genuinely cannot be distinguished from a turn whose
end-of-utterance was lost. Guessing would be worse than saying so.

---

## Pass criteria

Anything unticked stops the publish.

- [ ] Run A: `agent_initiated` is exactly **1** and it is the greeting.
      **This is the one that matters.** The first attempt at this classified on
      `SpeechCreatedEvent.user_initiated` and reported *every* speech as agent-initiated,
      because a cascaded pipeline sets that flag to `True` on ordinary replies too. If this
      count is anything other than 1, the classifier is wrong again and nothing gets pushed.
- [ ] Run A: `responded` is greater than 0. A zero here is the failure mode above.
- [ ] Run A: `incomplete` is **0**.
- [ ] Run A: `data/metrics/<runA>.jsonl` is non-empty. An empty capture means the
      `metrics_collected` stream stopped firing, which ends the method on this SDK.
- [ ] Run A: at least **10** responded turns.
- [ ] Run B: `speculative` is greater than 0.
- [ ] Replay of Run A reproduces the live ledger exactly.
- [ ] `--scramble` changes no TTFA value.
- [ ] The 24 Aug replay flags the 16.350s turn as retried.

## Findings to write down rather than fix

These are differences from the 1.7.0 session. They belong in the README, not in a bug fix.

- **`LLM calls per responded turn`.** It was 2.1 on 1.7.0, because `preemptive_generation`
  is on by default. If 1.8.0 reports 1.0, the default changed and that is worth a line.
- **The endpointing floor.** It pinned near 0.578s on 1.7.0, set by Silero
  `min_silence_duration=0.55` rather than by `endpointing_ms` or `min_delay`. A different
  floor means something moved in the turn-taking path.
- **`transcription_delay` spread.** It varied fourfold on one speaker on 1.7.0. Report the
  1.8.0 spread whatever it is, including if it is now stable.

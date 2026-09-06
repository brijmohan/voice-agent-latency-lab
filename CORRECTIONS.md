# Corrections

Claims I made during this work that turned out to be wrong, kept rather than quietly edited.

The point of a measurement repo is that its numbers can be trusted. That is worth more if
the record shows what happened when a claim did not survive checking.

---

### The turn detector reads the transcript

**Claimed:** `inference.TurnDetector()` runs a transformer over the transcript so far and
returns P(end of turn), so a syntactically incomplete sentence scores low.

**Wrong.** It is an audio model. It encodes the caller's audio directly and scores prosody
alongside content. The transcript-based `MultilingualModel` that behaves as described is
deprecated and slated for removal in SDK 2.0.

**How I found out:** two near-identical utterances in my own log, "Wait." five times scoring
0.915 and four times scoring 0.068. No transcript model can produce that pair.

**Cause:** I described the SDK from a document written for an earlier version instead of
checking the current docs.

---

### `endpointing_ms=25` means the agent replies 25ms after you stop

**Wrong.** Measured `transcription_delay` ranged 0.257s to 1.031s at that setting.
`endpointing_ms` is one input to the provider's finalisation decision, alongside its own
acoustic model, internal buffering, and network transit both ways.

---

### The Smart Turn threshold in `huggingface/speech-to-speech` is hardcoded

**Wrong.** It is a documented CLI argument, `--smart_turn_threshold`, plumbed from
`vad_arguments.py` through `vad_handler.py`. I read the constructor default and stopped.

**Nearly consequential:** this was about to be posted as a public issue on someone else's
repository.

---

### Tool calling is broken on that project's default macOS path

**Wrong, and the framing mattered more than the facts.** The observation was real: 14 tool
call attempts, 14 rejections. But the prompt template explicitly instructs "Use named
arguments only", and the parser warns-and-drops violations deliberately.

The accurate claim is narrower: **the default local model ignores that instruction.**
Isolated testing, 10 queries each: Qwen3-4B-Instruct-2507 emitted positional calls 9/10 at
4-bit and 8/10 at 8-bit. Not a quantisation artefact.

**Cause:** read one file, not the prompt that governs it.

---

### Turns producing no audio but logging `status=completed` is a defect

**Wrong.** A tool-call response legitimately produces no audio; the audio arrives in the
follow-up response after the tool result. My test tool declared a required parameter, every
positional call was rejected, so the follow-up never came and I read the broken setup's
artefact as a property of the code.

Fixed by declaring no required parameters, which let the call validate and execute. The
correct shape then appeared: `tts_ttfa=n/a` on the tool-call record, real timings on the
answer record.

---

## The pattern

Five corrections, four of them the same mistake: **reading source in isolation and not
checking it against a running system or the configuration that governs it.**

The habit that came out of it, and the reason the numbers above are worth reading:

1. Run it before claiming it.
2. Read the prompt, the config and the CLI surface, not just the function.
3. Search the issue tracker before reporting anything as new.
4. State the commit. A claim without one cannot be checked.
5. Say what was not tested.

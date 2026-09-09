# Replay corpus: recording script

23 utterances, recorded once, replayed as many times as you like. This is what makes
n>=100 reachable and therefore P99 reportable for the first time, on either stack.

## Why recorded human speech and not TTS

Smart Turn is an **acoustic** model scoring prosody. Measuring it with synthetic speech is
close to circular: you would be testing how the detector responds to a vocoder, not to a
person. The first wire-level turn used macOS `say` and its `p=0.736` says nothing about
human speech.

Recording once and replaying preserves the acoustics while making runs byte-identical
between configurations. That is the whole point: when you change a threshold and the number
moves, the speaker did not move.

## What this corpus cannot tell you

Replaying 23 utterances 5 times gives **system variance, not population variance.** A P99
computed this way is the tail of the machine under a fixed input, which is exactly what you
want for tuning and for regression gates. It is *not* the tail across callers. Say so
wherever the number appears, or the first reviewer who notices will say it for you.

## Design note: record the French set later, same structure

The block structure below is language-independent on purpose. Recording a French version
with the same 23 functions gives a **parallel bilingual corpus**, which is the actual asset
behind "multilingual turn-taking evaluation" rather than a claim about one. Do English now,
because both stacks are currently configured for it. Keep the IDs identical when you do
French so the pairs line up.

---

## How to record

- Quiet room. Same microphone, same distance (about 20cm), **one sitting**. Do not change
  input gain between takes.
- QuickTime Player, File > New Audio Recording. **One file per utterance.**
- In each take: start recording, stay silent for **1 second**, speak the line, then stay
  silent for **1.5 seconds**, stop.
- Speak it the way you would say it on a phone call. Do not perform it. Prosody is the
  measurement.
- Fluffed a take? Re-record the whole file. Do not edit, trim, normalise or denoise
  anything. The prep script handles all of that deterministically.
- Save into `labs/lab02-wire-harness/recordings/raw/` using the **ID as the filename**,
  e.g. `A01.m4a`.

---

## Block A: clean baseline (6)

Falling pitch, definite full stop. The easy case, and the reference condition.

| ID | Say | Tests |
|---|---|---|
| A01 | "Hello, I'd like to book a ceremony slot." | opening turn, medium length |
| A02 | "Yes, that works for me." | short, clean |
| A03 | "The first name is Edward Hawkins." | proper nouns |
| A04 | "Could you tell me what dates you have available in June?" | long utterance, question intonation but complete |
| A05 | "I'd prefer the morning if that's possible." | medium, hedged |
| A06 | "That's everything, thank you very much." | closing turn |

## Block B: incomplete prosody (4)

**The most important block.** End each line sounding genuinely unfinished, pitch level or
rising, as though the next word is coming. Then simply stop and go quiet.

A correct turn detector should **hold** on all four. Every one it commits is a false
endpoint, which in a real call means cutting the caller off mid-sentence.

| ID | Say | Tests |
|---|---|---|
| B01 | "The date I was thinking of was" | trailing off mid-clause |
| B02 | "And the second name is" | incomplete noun phrase |
| B03 | "So if Saturday doesn't work then" | incomplete conditional |
| B04 | "I wanted to ask about" | dangling preposition |

## Block C: mid-utterance pause (3)

Pause about **600ms** at each `…`, keeping pitch up so it reads as hesitation rather than
completion. These generate speculative generations that get discarded, which is where the
LLM bill goes.

| ID | Say | Tests |
|---|---|---|
| C01 | "The first name is Edward … Hawkins." | one internal pause |
| C02 | "I'd like to book … a ceremony slot … for the fourteenth of June." | two internal pauses |
| C03 | "My email is edward … at … northgate dot example dot com." | three, over an address |

## Block D: disfluency (3)

Filled pauses and self-correction, said naturally.

| ID | Say | Tests |
|---|---|---|
| D01 | "Um, I think, uh, the twenty first would be better." | filled pauses |
| D02 | "Saturday the fourteenth. No, sorry, the twenty first." | self-correction after a complete clause |
| D03 | "Can we do, actually, let me check, yes, the fourteenth." | mid-utterance repair |

## Block E: transcription stress (4)

Aimed at the first stage of the budget, which is the least stable one you measured.

| ID | Say | Tests |
|---|---|---|
| E01 | "Saturday the twenty first of June at eleven in the morning." | dates and times |
| E02 | "My number is zero seven, four four six, three nine one, two eight." | digit strings |
| E03 | "That's E, D, W, A, R, D, at northgate dot example dot com." | spelled-out letters |
| E04 | "We'd like the salle des mariages, at Northgate." | code-switching into French |

## Block F: minimal turns (3)

The fast end of the distribution. Very short utterances give the endpointer least to work with.

| ID | Say | Tests |
|---|---|---|
| F01 | "Yes." | one word |
| F02 | "No, thank you." | two words |
| F03 | "That's right." | short confirmation |

---

## When you are done

Drop the files in `recordings/raw/` named `A01.m4a` ... `F03.m4a` and say so. The prep
script converts to 16 kHz mono PCM16, reports the duration and how much trailing silence it
trimmed from each, and normalises every file to an identical trailing-silence tail so that
endpointing is measured under the same conditions for every utterance.

Nothing here contains a real name, a real address, a real phone number or a client name.
Keep it that way if you add utterances.

---

## Part two

Blocks G, H and I, covering tool calling and contact fidelity, are in
[recording-script-tools.md](recording-script-tools.md). Record them with the same
microphone, room and distance, or blocks A to F stop being a control.

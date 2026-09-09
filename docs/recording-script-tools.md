# Replay corpus, part two: tool calling and contact fidelity

Extends `docs/recording-script.md` with three blocks. Same speaker, same protocol, same
naming. **Record with the same microphone, room and distance as blocks A to F**, or the two
halves cannot be compared and the original 23 stop being a control.

Protocol is unchanged: one file per utterance, 1 second of silence before, 1.5 seconds after,
no editing. Save into `recordings/raw/` named by ID.

---

## Why these blocks exist

**Block G validates a fix.** A local model emits positional tool calls most of the time,
because tools are rendered to it as Python signatures. Those arguments used to be discarded,
so the call silently became empty. The binding fix is covered by unit tests; block G is what
proves it survives a real model producing real calls from real speech. **This is the block to
record if you record only one.**

**Block H is a benchmark, not a test.** Contact capture is the highest-stakes, lowest-accuracy
field in a booking, and it fails silently: the caller hears a confirmation and no invitation
ever arrives. Existing evidence, 5 identical replays each:

| Spoken | Heard |
|---|---|
| "edward at **northgate** dot example dot com" | `edward at northcate.example.com` |
| "E, D, W, A, R, D" spelled out | `e D W A R D`, not assembled |
| "salle des mariages" | `salle de mariage` |
| a ten-digit number | exact |

**Block I** covers the readback that turns a silent failure into a loud one.

---

## Block G: tool invocation (6)

Speak these as ordinary requests, clean prosody, full stop at the end. The point is the tool
call the agent makes, not how you said it.

| ID | Say | What it should trigger | Why |
|---|---|---|---|
| G01 | "What dates do you have available in June?" | `list_slots(month)` | one argument, the simplest call |
| G02 | "Is Saturday the fourteenth still free?" | `check_slot(date)` | a specific date rather than a range |
| G03 | "I'd like to book Saturday the fourteenth at ten in the morning." | `hold_slot(date, time)` | **two arguments in order. This is the case the binding fix exists for.** |
| G04 | "Could you check the twenty first as well?" | a second call in one turn | multiple tool calls, and the follow-up turn |
| G05 | "Actually, what do you have in July instead?" | `list_slots` with a changed argument | the model must not reuse the earlier value |
| G06 | "Book it." | a call with arguments carried from context | no arguments spoken at all; the hardest case, and the one most likely to produce a wrong booking |

## Block H: contact fidelity (8)

The benchmark block. Say each once, naturally, at your ordinary phone pace.

| ID | Say | Tests |
|---|---|---|
| H01 | "My email is edward dot hawkins at gmail dot com." | the common case, a well-known domain |
| H02 | "That's E, D, W, A, R, D, at gmail dot com." | spelled letters, which previously did not assemble |
| H03 | "My email is edward at orange point fr." | a French domain, said the French way |
| H04 | "It's edward at laposte dot net." | a French domain an English model has never seen |
| H05 | "My number is zero six, twelve, thirty four, fifty six, seventy eight." | **French numbers said in pairs.** Digit-by-digit already transcribes perfectly; the pair form is how people actually say a French mobile and is untested |
| H06 | "Zero six, one two, three four, five six, seven eight." | the same number digit by digit, as a control against H05 |
| H07 | "Edward at northgate dot example dot com." | the known failure, kept as a regression case |
| H08 | "N for November, O for Oscar, R for Romeo, T for Tango, H for Hotel." | phonetic spelling, the mitigation |

Use these exact fictional values. No real address, no real number.

## Block I: readback and correction (4)

| ID | Say | Tests |
|---|---|---|
| I01 | "Yes, that's correct." | confirmation |
| I02 | "No, that's not right." | rejection, which must not be heard as acceptance |
| I03 | "No, it's northgate with a G, not a C." | spoken correction of the exact known failure |
| I04 | "Could you read that back to me?" | the readback request itself |

---

## What to measure

Block G, on the tool call:

- **Bound correctly.** The arguments arrive named and in the right slots. Run once against
  the unpatched parser and once against the fix so the difference is measured, not assumed.
- **Positional rate.** How often the model emits positional rather than named arguments.
  Prior measurement was 9 in 10 at 4-bit; this is the same figure on real speech.
- **Call correctness.** Right tool, right values.

Block H, and the metric that matters:

- **Silent-failure rate:** the booking is confirmed and the contact is undeliverable. Any
  other metric can look acceptable while this one is unacceptable.
- Exact match on the normalised contact.
- Whether the readback in block I catches the failures block H produces.

A word error rate is the wrong measure here. 6% that mangles a domain is worse than 12% that
does not.

## Pass criteria before the package is published

- [ ] G03 produces a correctly bound two-argument call from real speech.
- [ ] No tool call is silently dropped across all six G utterances.
- [ ] Block H's silent-failure rate is recorded, whatever it is. A bad number that is written
      down is a result; an unmeasured one is a liability.
- [ ] H05 against H06 says whether French pair-form numbers are safe. If they are not, the
      phone number stops being the reliable identifier and the whole contact design changes.

That last one is the finding that could change the design, so it is worth recording even if
nothing else is.

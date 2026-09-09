# Tool calling and contact capture, measured on real speech
*9 Sep 2026. `speech-to-speech` v1.0.0 fully local on an M3 Pro, Qwen3-4B-Instruct 4-bit,
Parakeet TDT. 18 recorded utterances, one speaker, English.*

Two experiments. The first tests a fix. The second is a benchmark, and it is the one that
should change a design.

---

## 1. Tool calls: 0 of 6 became 5 of 6

Three booking tools were declared over the wire (`list_slots(month)`, `check_slot(date)`,
`hold_slot(date, time)`) and six recorded requests were replayed against the same server on
two branches.

| Utterance heard | Unpatched | With positional binding |
|---|---|---|
| "What dates do you have available in June?" | no call | `list_slots({"month": "June"})` |
| "Is Saturday the fourteenth still free?" | no call | `check_slot({"date": "Saturday 14 June"})` |
| "I'd like to book Saturday the fourteenth at ten in the morning." | no call | `check_slot({"date": "Saturday 14 June"})` |
| "Could you check the twenty-first as well?" | no call | `check_slot({"date": "Saturday 21 June"})` |
| "Actually, what do you have in July instead?" | no call | `list_slots({"month": "July"})` |
| "Book it." | no call | no call |

**The unpatched server produced no tool call at all**, and its log shows why:

```
Dropping positional arguments for 'list_slots': {'__arg_0__'}
Skipping invalid tool call: Missing required parameters for 'list_slots': {'month'}
```

The model emitted the call correctly. The parser discarded the argument, validation then
found the required parameter missing, and the call was dropped before reaching the client.
The caller experiences an assistant that cannot do anything.

The patched run logs **no warnings of any kind**: every call the model emitted bound and
was delivered.

### What this does not show

**`hold_slot` was never invoked, so two-argument binding is still unproven on real speech.**
Asked to *book* a slot, the model called the read-only `check_slot` with one argument
instead. The multi-argument path is covered by unit tests and by nothing else.

That substitution is a finding in its own right. For a public service the failure is in the
safe direction, since checking is harmless where booking is not, but it means the booking
path does not work as scripted. Whether the cause is the instruction wording, the tool
description, or model conservatism is not established.

"Book it." produced no call in either condition, which is expected: no arguments were
spoken and the tool had to be inferred entirely from context.

---

## 2. Contact capture: no email survived

| Said | Heard | Outcome |
|---|---|---|
| "edward dot hawkins at gmail dot com" | `edward.hawkins at gmail dot com` | "at" and "dot com" left as words |
| "E, D, W, A, R, D, at gmail dot com" | `edw ard at gmail.com` | **spelled letters collapsed, with a space** |
| "edward at orange point fr" | `edward erobasorange.fr` | **"at" heard as "erobas", glued to the domain** |
| "edward at laposte dot net" | `Edward at laPost.net` | **wrong domain, silently undeliverable** |
| "edward at northgate dot example dot com" | `Edward at northgate.example.com` | correct this time, see below |
| "N for November, O for Oscar, R for Romeo..." | verbatim, unassembled | recoverable |

**Not one of the five addresses transcribed into a syntactically valid form.** Even the best
case needs a normalisation layer, and three of them lose information no layer can recover.

### The mitigation that made it worse

Spelling the address out, the obvious human strategy, was the **worst** performer:
`E, D, W, A, R, D` became `edw ard`. The letters were not assembled, and a spurious space was
inserted. Recommending "just spell it out" would have made the system less reliable, not more.

The phonetic alphabet was the only spoken form that lost nothing: it transcribes verbatim and
an assembler can recover the letters. It is also slow and most callers will not do it.

### Phone numbers survive both ways, and that settles the design

| Said | Heard |
|---|---|
| "zero six, twelve, thirty four, fifty six, seventy eight" (French pair form) | `zero six twelve thirty-four fifty-six seventy-eight` |
| "zero six, one two, three four, five six, seven eight" (digit by digit) | `Zero six, one, two, three, four, five, six, seven, eight` |

Both are correct as words and need only a word-to-digit conversion. **The French pair form,
which is how people actually say a mobile number, is safe.** That was the open question, and
it confirms the phone number as the reliable identifier rather than the email address.

---

## 3. A correction to an earlier claim

An earlier experiment found "northgate" transcribed as "northcate" in five out of five runs,
and that was described as a deterministic failure. Accurate for what it measured, five replays
of one recording, and easy to over-read.

**A different take of the same word transcribed correctly.** Replaying one file gives
identical results every time, which is a property of the system. Saying the same word again
can give a different result, which is a property of the speaker. The determinism is in the
replay, not in the word, and the earlier phrasing invites the wrong reading.

This makes contact capture worse rather than better as a design problem: an error that
reproduces is one you can detect and correct, and one that depends on how a caller happened
to pronounce a word on the day cannot be caught by testing.

---

## 4. What follows

1. **Do not use a spoken email address as the primary identifier.** Take the phone number,
   which survived both spoken forms intact.
2. **A normalisation layer is mandatory, not optional**, if an address is captured at all:
   "at" to `@`, "dot" to `.`, whitespace stripped, plus the French "arobase". Even then,
   `laPost.net` and `erobasorange.fr` remain wrong.
3. **Domain allowlist snapping** would repair the `laPost` class outright and is the highest
   value single mitigation.
4. **Readback is not optional.** Three of five addresses were wrong in ways only the caller
   can catch.
5. **The two-argument tool path still needs a real test.** Either reword the instruction so
   the model reaches for `hold_slot`, or exercise it with a tool that has no single-argument
   alternative.

## Reproduce

The probe, the tool schemas and the raw results are in the workspace rather than this
repository, since they depend on recordings that are not published. The recording script is
`docs/recording-script-tools.md`.

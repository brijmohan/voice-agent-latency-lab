# Defects found in measured systems

Measuring a stack from outside turns up faults in it. This tracks them, their evidence, and
their status, so that nothing found here is quietly lost.

A finding is only listed once it has been reproduced against a named commit, with a
before-and-after where a fix exists. A suspicion is not a finding.

---

## 1. Positional tool-call arguments are discarded, so tool calls vanish

**System:** `huggingface/speech-to-speech` v1.0.0 (`16d7f98`)
**Severity:** high. Every affected call is lost with no error visible to the client.
**Status:** fix written and tested on `brijmohan:fix/positional-tool-args`. **Not filed
upstream.** See *Why this is not filed yet* below.

### The defect

`_parse_call_expr` captures positional arguments in order as `__arg_0__`, `__arg_1__`, and
`to_realtime_function_tool_call` then drops them. Validation reports the required parameter
missing and the call is skipped:

```
WARNING - Dropping positional arguments for 'list_slots': {'__arg_0__'}
WARNING - Skipping invalid tool call: Missing required parameters for 'list_slots': {'month'}
```

Nothing reaches the client. The caller experiences an assistant that will not act, with no
error anywhere in the chain.

### Why the model emits that form

`FunctionTool.to_code_prompt` renders each tool to the model as a Python signature, so a
model shown `def hold_slot(date: str, time: str)` answers
`hold_slot("Saturday 14 June", "10:00")`. **The prompt format elicits precisely the form the
parser discards.** Instructing the model to use named arguments only did not prevent it.

The order is not ambiguous: `signature_from_schema` builds that rendered signature by
iterating `properties` in declaration order, so the Nth positional argument is the Nth
declared property, and the schema is already in scope where the arguments are dropped.

### Evidence

Six recorded human utterances replayed through `serve` against the same local stack, on
`main` and on the fix branch: **0 of 6 calls survived on `main`, 5 of 6 on the branch**, with
no warnings on the branch. Full table in `REPORT-speech-to-speech.md` section 7.

### Why this is not filed yet

Deliberate, and revisit rather than forget. The finding is carried in the measurement report
instead, where it arrives as a result rather than as a review request. The maintainer can ask
for the patch if he wants it, at which point the branch is already pushed and tested.

**Revisit if:** the report has been sent and there is no response within a few weeks, or
someone else reports the same defect, or an upstream release touches this code path.

---

## 2. `signature_from_schema` rejects a valid JSON Schema

**System:** same. **Severity:** low, and it has a trivial workaround.
**Status:** reproduced, not written up, not filed.

It builds an `inspect.Signature` by iterating `properties` in declaration order and assigning
defaults to anything not required. Python forbids a non-default parameter after a defaulted
one, so a schema listing an optional property *before* a required one raises:

```
ValueError: non-default argument follows default argument
```

The schema is valid; JSON Schema has no ordering requirement. Any tool whose properties
happen to be declared in that order cannot be rendered into a prompt at all.

Deserves its own issue rather than being bundled with finding 1, since bundling would widen
that review for an unrelated cause.

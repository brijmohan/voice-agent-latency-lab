"""Print the latency table for a directory of wire captures.

    apresvous report <dir> [--csv out.csv]

Reporting only. Every number here comes from `apresvous.wire_correlator.correlate`; this
file decides nothing about what a turn is.
"""

import csv
import json
import statistics as st
import sys
from collections import Counter
from pathlib import Path

from apresvous.wire_correlator import TurnOutcome, correlate

STAGES = ("hold", "llm", "continuation", "tts", "ttfa")


def pct(values, q):
    v = sorted(values)
    k = (len(v) - 1) * q
    f = int(k)
    return v[f] if f + 1 >= len(v) else v[f] + (k - f) * (v[f + 1] - v[f])


def main(path, csv_out=None):
    rows = []
    for p in sorted(Path(path).glob("*.jsonl")):
        events = [json.loads(line) for line in p.open()]
        for turn in correlate(events):
            rows.append((p.stem, turn))
    if not rows:
        print(f"no captures in {path}")
        return 1

    counts = Counter(t.outcome for _, t in rows)
    print(f"{len(rows)} turn(s) from {len({n for n, _ in rows})} capture(s)")
    for outcome in TurnOutcome:
        if counts[outcome]:
            print(f"  {outcome.value:<16} {counts[outcome]}")

    ok = [t for _, t in rows if t.outcome is TurnOutcome.RESPONDED]
    if not ok:
        print("\nno responded turns, nothing to summarise")
        return 0

    print(f"\nRESPONDED turns, n = {len(ok)}")
    print(f"  {'stage':<13}{'P50':>8}{'P90':>8}{'P99':>8}{'max':>8}")
    for stage in STAGES:
        v = [getattr(t, stage) for t in ok]
        print(f"  {stage:<13}{pct(v, .50):8.3f}{pct(v, .90):8.3f}{pct(v, .99):8.3f}{max(v):8.3f}")
    multi = sum(1 for t in ok if t.responses > 1)
    if multi:
        print(f"\n  {multi} turn(s) took more than one response (tool call or continuation)")

    print(f"\n  revisions per turn: mean {st.mean(t.revisions for t in ok):.2f}, "
          f"max {max(t.revisions for t in ok)}")
    print(f"  segments  per turn: mean {st.mean(t.segments for t in ok):.2f}, "
          f"max {max(t.segments for t in ok)}")
    print("\n  P99 here is pooled across utterances, so it partly reflects which sentence was")
    print("  spoken rather than a system tail. Per-utterance spread is the honest variance.")

    if csv_out:
        with open(csv_out, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["capture", "outcome", "segments", "revisions", *STAGES, "transcript"])
            for name, t in rows:
                w.writerow([name, t.outcome.value, t.segments, t.revisions,
                            *(getattr(t, s) for s in STAGES), t.final_transcript])
        print(f"\nwrote {csv_out}")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    out = args[args.index("--csv") + 1] if "--csv" in args else None
    sys.exit(main(args[0], out))

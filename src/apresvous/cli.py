"""Command line entry point.

    apresvous prep    recordings/raw recordings/wav
    apresvous capture recordings/wav captures --repeats 5
    apresvous report  captures [--csv out.csv]

Subcommands are imported lazily. `capture` needs websockets and a running server; `report`
and `prep` need neither, and a user who only wants to re-analyse existing captures should
not pay for the rest.
"""

import argparse
import sys

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="apresvous",
        description="Measure when a voice agent decides it is your turn.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_prep = sub.add_parser("prep", help="convert recordings to a replay corpus")
    p_prep.add_argument("raw_dir")
    p_prep.add_argument("out_dir")

    p_cap = sub.add_parser("capture", help="replay a corpus through a Realtime endpoint")
    p_cap.add_argument("wav_dir")
    p_cap.add_argument("out_dir")
    p_cap.add_argument("--repeats", type=int, default=1)
    p_cap.add_argument("--url", default="ws://127.0.0.1:8765/v1/realtime")
    p_cap.add_argument("--only", default=None, help="comma-separated utterance ids")

    p_rep = sub.add_parser("report", help="print the latency table for a capture directory")
    p_rep.add_argument("capture_dir")
    p_rep.add_argument("--csv", default=None)

    args = parser.parse_args(argv)

    if args.command == "prep":
        from apresvous.prep import main as prep_main
        return prep_main(args.raw_dir, args.out_dir)

    if args.command == "capture":
        import asyncio

        from apresvous.capture import run
        return asyncio.run(run(args.wav_dir, args.out_dir, repeats=args.repeats,
                               url=args.url, only=args.only))

    from apresvous.report import main as report_main
    return report_main(args.capture_dir, args.csv)


if __name__ == "__main__":
    sys.exit(main())

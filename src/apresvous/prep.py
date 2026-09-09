"""Convert QuickTime takes into a byte-identical replay corpus.

    apresvous prep recordings/raw recordings/wav

Every file becomes 16 kHz mono PCM16 with an identical 300ms lead and its trailing silence
removed. The capture client appends its own fixed tail, so endpointing is measured under the
same conditions for every utterance rather than under whatever pause the take happened to
end with. Variable trailing silence would otherwise land directly in the endpointing number.

Reports what it trimmed from each file so you can eyeball it rather than trust it.
"""

import subprocess
import sys
import wave
from array import array
from pathlib import Path

RATE = 16000
LEAD_MS = 300
THRESH_DBFS = -45.0   # conservative: a soft final consonant must survive
GUARD_MS = 100        # keep this much beyond the last sample above threshold
RAW_EXT = {".m4a", ".mov", ".wav", ".aiff", ".aif", ".caf", ".mp4"}


def to_pcm16(src: Path, dst: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-ac", "1", "-ar", str(RATE), "-sample_fmt", "s16", "-c:a", "pcm_s16le", str(dst)],
        check=True,
    )


def read_samples(path: Path):
    with wave.open(str(path)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, RATE)
        raw = w.readframes(w.getnframes())
    return memoryview(raw).cast("h")


def speech_bounds(s):
    """First and last sample index above the threshold, or None if the take is silent."""
    thresh = 32768 * (10 ** (THRESH_DBFS / 20.0))
    first = last = None
    for i, v in enumerate(s):
        if abs(v) > thresh:
            if first is None:
                first = i
            last = i
    return first, last


def write_wav(path: Path, samples) -> None:
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(samples.tobytes() if hasattr(samples, "tobytes") else bytes(samples))


def main(raw_dir: str, out_dir: str) -> int:
    raw, out = Path(raw_dir), Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / ".tmp.wav"

    files = sorted(p for p in raw.iterdir() if p.suffix.lower() in RAW_EXT)
    if not files:
        print(f"no recordings found in {raw}")
        return 1

    print(f"{'id':<6}{'orig':>8}{'speech':>8}{'lead cut':>10}{'tail cut':>10}  note")
    print("-" * 56)
    bad = 0
    for src in files:
        to_pcm16(src, tmp)
        s = read_samples(tmp)
        orig_s = len(s) / RATE
        first, last = speech_bounds(s)
        if first is None:
            print(f"{src.stem:<6}{orig_s:8.2f}{'-':>8}{'-':>10}{'-':>10}  SILENT, re-record")
            bad += 1
            continue

        guard = int(RATE * GUARD_MS / 1000)
        start = max(0, first - guard)
        end = min(len(s), last + guard)
        lead = array("h", [0]) * int(RATE * LEAD_MS / 1000)
        write_wav(out / f"{src.stem}.wav", lead + array("h", s[start:end]))

        speech_s = (end - start) / RATE
        note = ""
        if start == 0:
            note = "clipped at start, re-record with more lead"
            bad += 1
        if end == len(s):
            note = (note + "; " if note else "") + "no trailing silence, re-record"
            bad += 1
        print(f"{src.stem:<6}{orig_s:8.2f}{speech_s:8.2f}{start/RATE:10.2f}{(len(s)-end)/RATE:10.2f}  {note}")

    tmp.unlink(missing_ok=True)
    print(f"\n{len(files)} file(s) -> {out}")
    if bad:
        print(f"{bad} problem(s) above. Re-record those takes.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))

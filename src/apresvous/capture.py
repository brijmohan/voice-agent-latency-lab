"""Replay the recorded corpus through a Realtime endpoint, capturing wire events.

    apresvous capture recordings/wav captures --repeats 5

One fresh WebSocket session per utterance, because a reused session accumulates
conversation history and the LLM stage would then grow across the run.

Silence keeps streaming after the utterance, for the whole wait, exactly as it would on a
live call. An endpointer that stops receiving audio is not the endpointer you deploy. This
also means an utterance the detector holds is recorded with the duration of the hold rather
than as a failure: for the deliberately-incomplete lines, that hold is the measurement.

Captures events only. Deciding what counts as a turn, a hold or a failure is correlation
and belongs in the correlator, not here.
"""

import asyncio
import base64
import json
import time
import wave
from pathlib import Path

import websockets

RATE = 16000
CHUNK_MS = 20
MAX_WAIT_AFTER_SPEECH_S = 15.0


def _session_update() -> dict:
    """Build the session handshake, preferring the server project's own builder.

    Imported lazily so `speech_to_speech` stays an optional extra: the correlator and the
    report need nothing from it, and a measurement library should not drag in the system it
    measures. When it is installed the handshake is the project's own rather than a
    reimplementation of it, which is what makes the capture faithful.

    The fallback omits `format` deliberately. At 16 kHz the upstream builder omits it too, to
    select the server's native pipeline rate, so sending an explicit format here would change
    the thing being measured.
    """
    try:
        from speech_to_speech.api.openai_realtime.audio_client import (
            RealtimeAudioClientConfig,
            build_session_update,
        )
    except ImportError:
        return {"type": "session.update",
                "session": {"type": "realtime", "audio": {"input": {}, "output": {}}}}
    return build_session_update(RealtimeAudioClientConfig())


def load_pcm(path: Path) -> bytes:
    with wave.open(str(path)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, RATE), path
        return w.readframes(w.getnframes())


async def open_session(url: str, attempts: int = 12):
    """Connect and receive `session.created`, retrying while the pipeline slot is busy.

    The server releases its slot shortly *after* the previous socket closes (measured at
    48ms here), and it signals exhaustion by accepting the socket and then sending a 1008
    close. So the handshake succeeds and the rejection only surfaces on the first exchange,
    which means the retry has to wrap connect plus first receive, not connect alone.

    Sequential replay is deliberate: concurrent sessions contend for the MLX lock and would
    contaminate every timing. The fix is to wait for the slot, not to widen the pool.
    """
    delay = 0.25
    for attempt in range(attempts):
        ws = None
        try:
            ws = await websockets.connect(url, subprotocols=["realtime"], max_size=None)
            first = json.loads(await ws.recv())
            return ws, first
        except Exception as exc:
            if ws is not None:
                await ws.close()
            if "slots in use" not in str(exc):
                raise
            if attempt == 0:
                print(f"    (slot busy: {type(exc).__name__}, retrying)", flush=True)
            if attempt == attempts - 1:
                raise RuntimeError(f"pipeline slot never freed after {attempts} attempts")
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 3.0)


async def capture_one(url: str, wav: Path, out: Path) -> dict:
    pcm = load_pcm(wav)
    chunk_bytes = int(RATE * 2 * CHUNK_MS / 1000)
    silence = b"\x00\x00" * (chunk_bytes // 2)
    events: list[dict] = []
    done = asyncio.Event()
    t0 = time.monotonic()

    def record(ev: dict) -> None:
        events.append({"t": round(time.monotonic() - t0, 6), "type": ev.get("type"), "event": ev})

    ws, first = await open_session(url)
    async with ws:
        record(first)
        await ws.send(json.dumps(_session_update()))

        async def receiver():
            try:
                while True:
                    ev = json.loads(await ws.recv())
                    record(ev)
                    if ev.get("type") == "response.done":
                        done.set()
                        return
            except websockets.ConnectionClosed:
                done.set()

        rx = asyncio.create_task(receiver())
        started = time.monotonic()
        sent = 0

        async def send_chunk(data: bytes) -> None:
            nonlocal sent
            await ws.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(data).decode(),
            }))
            sent += len(data)
            drift = started + sent / (RATE * 2) - time.monotonic()
            if drift > 0:
                await asyncio.sleep(drift)

        for i in range(0, len(pcm), chunk_bytes):
            await send_chunk(pcm[i:i + chunk_bytes])
        speech_ended = time.monotonic() - t0

        # keep the line open with silence, as a real call would
        while not done.is_set() and (time.monotonic() - t0) - speech_ended < MAX_WAIT_AFTER_SPEECH_S:
            await send_chunk(silence)

        if not done.is_set():
            record({"type": "_capture.timeout",
                    "waited_s": round((time.monotonic() - t0) - speech_ended, 3)})
        rx.cancel()

    meta = {"utterance": wav.stem, "wav": str(wav), "speech_ended_t": round(speech_ended, 6),
            "n_events": len(events)}
    with out.open("w") as fh:
        fh.write(json.dumps({"type": "_capture.meta", "t": 0.0, "event": meta}) + "\n")
        for e in events:
            fh.write(json.dumps(e) + "\n")
    return meta


async def run(wav_dir: str, out_dir: str, *, repeats: int = 1,
              url: str = "ws://127.0.0.1:8765/v1/realtime", only: str | None = None) -> int:
    """Replay every wav in *wav_dir* *repeats* times, writing one capture per turn."""
    wavs = sorted(Path(wav_dir).glob("*.wav"))
    if only:
        keep = {s.strip() for s in only.split(",")}
        wavs = [w for w in wavs if w.stem in keep]
    if not wavs:
        print(f"no .wav files in {wav_dir}")
        return 1

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    for r in range(1, repeats + 1):
        for wav in wavs:
            target = out / f"{wav.stem}-r{r}.jsonl"
            t = time.monotonic()
            meta = await capture_one(url, wav, target)
            print(f"  r{r} {wav.stem:<5} {meta['n_events']:>4} events  "
                  f"{time.monotonic() - t:5.1f}s  -> {target.name}", flush=True)
            # The server releases its pipeline slot ~50ms after the socket closes; settling
            # here keeps the retry path an exception rather than the normal case.
            await asyncio.sleep(1.0)
    print(f"done in {(time.monotonic() - started) / 60:.1f} min")
    return 0

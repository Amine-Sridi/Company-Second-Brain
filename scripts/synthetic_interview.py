"""
scripts/synthetic_interview.py
-------------------------------
Drives one complete VoiceHR interview end to end without a human or a browser, so the
one remaining [NEXT] item -- "a fresh, complete, five-section interview reaching Section
E, producing a real interaction_spontaneity score and a webhook RWS actually applies" --
can finally be observed.

It speaks the same protocol the browser does (backend/api/routes/session.py,
services/gemini_live.py), nothing special-cased for tests:

    POST /api/interview/{token}/start      -> flips status pending->active, returns ws_url
    WS   /ws/interview/{token}
      client -> server : binary frames of raw PCM, 16 kHz mono s16le
                         text {"type":"speech_end"}  after each utterance
                         text {"type":"heartbeat"}   to keep the socket alive
      server -> client : {"type":"task", task, total, follow_up, phase}
                         {"type":"transcript_delta", role, text}
                         {"type":"audio_start"|"audio_end"|"conversation_terminated"|...}

Gemini Live is real here: real audio in, real audio out, real turn-taking. The only
thing simulated is the candidate's voice.

**What this does and does not prove.** It proves the *pipeline*: that Section E fires,
that a real transcript reaches the evaluator, that interaction_spontaneity gets a real
score, that the webhook reaches RWS and the language gate computes. It does NOT validate
the rubric against a real human -- a TTS voice is fluent by construction, so a high CEFR
result here says the plumbing works, not that the scoring is calibrated.

TTS backends (--tts):
    sapi        Windows' built-in voice. Free, no key, no network. Use this first to
                prove the plumbing before spending credits.
    elevenlabs  Natural speech via ELEVENLABS_API_KEY, requested directly as
                pcm_16000 so no resampling is needed.

Run:
    python scripts/synthetic_interview.py --tts sapi --base-url http://localhost:8004
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

try:
    import httpx
    import websockets
except ImportError:  # pragma: no cover
    print("pip install httpx websockets", file=sys.stderr)
    raise

SAMPLE_RATE = 16_000
BYTES_PER_SAMPLE = 2
FRAME_MS = 20
FRAME_BYTES = SAMPLE_RATE * BYTES_PER_SAMPLE * FRAME_MS // 1000  # 640

# Deliberately substantive answers: Section E picks its follow-up tier from observed
# ability (agent.py: ADAPTIVE_FOLLOWUPS -- elaborate / reasoning / abstract), so a
# monosyllabic candidate would only ever exercise the "elaborate" tier and would likely
# be scored insufficient-evidence, which is the case this run is NOT trying to test.
ANSWERS = [
    "Hello, and thank you for having me. My name is Alex, and I have been working as a "
    "backend engineer for about five years now. Most of my work has been with Python, "
    "building REST APIs with FastAPI, and I have spent a lot of time on PostgreSQL "
    "performance and on deploying services with Docker.",

    "The project I am most proud of was a document processing pipeline that had become "
    "painfully slow. Requests were taking close to thirty seconds. I profiled it "
    "properly instead of guessing, and it turned out almost all of the time was spent "
    "in a loop making one database query per record. I rewrote it as a single batched "
    "query and added an index that was missing, and we brought it down to under two "
    "seconds. The lesson I took from it was to measure before optimising.",

    "When I disagree with a colleague about a technical decision, I try to separate the "
    "disagreement from the person. I ask what problem they are actually solving, "
    "because quite often we are optimising for different things without realising it. "
    "If we still disagree, I prefer to write both options down with the trade-offs and "
    "let the team decide, rather than letting the loudest voice win.",

    "In five years I would like to be the person other engineers come to when something "
    "is genuinely hard, and I would like to be mentoring more deliberately than I do "
    "today. I am less interested in a title than in working on systems where "
    "correctness really matters, because that is the work I find most satisfying.",

    # Section E: whichever adaptive tier is chosen, a thoughtful answer fits all three.
    "That is a good question. I think the honest answer is that it would look quite "
    "different from another team's point of view. An engineer optimising for delivery "
    "speed would probably see the same trade-off as unnecessary caution, and they would "
    "not be wrong exactly, they would just be weighing a different risk. What I have "
    "learned is that most of these arguments are really about which failure you are "
    "more afraid of.",
]

# After the five scripted answers, the candidate keeps talking with *substance*. The
# first version of this repeated "Sorry, could you repeat that?", which was actively
# harmful: Sarah treated every one as a comprehension failure and re-probed instead of
# moving on, so the interview never concluded and ran into Gemini Live's own session
# limit at ~13 minutes with no report. These are deliberately topic-agnostic so any one
# of them is a coherent reply to any follow-up.
FOLLOW_UPS = [
    "Yes, absolutely. A concrete example would be the caching layer we added last year. "
    "We were re-reading the same reference data on every request, and moving it into a "
    "short-lived in-memory cache cut our median response time roughly in half.",

    "I think the reasoning came down to risk. We could have shipped the faster version "
    "immediately, but it would have been much harder to reverse if it went wrong, so we "
    "chose the option we could undo cheaply.",

    "From another team's perspective it would probably look overly cautious. Someone "
    "optimising for delivery speed would weigh the same trade-off differently, and I do "
    "not think they would be wrong, just answering a different question.",

    "What I took away from it was that communication mattered more than the technical "
    "choice itself. Once we wrote the trade-offs down, the disagreement mostly resolved "
    "on its own.",

    "That is fair. If I were doing it again I would involve the operations team earlier, "
    "because they understood the failure modes far better than we did at the time.",

    "I would say my strongest area is debugging systems under load, and the thing I am "
    "actively working on is being more concise when I explain technical detail to "
    "non-engineers.",

    "Thank you, I have enjoyed this conversation. I think that covers what I wanted to "
    "say about my experience.",
]


# ---------------------------------------------------------------------------
# TTS backends -> 16 kHz mono s16le PCM
# ---------------------------------------------------------------------------

def tts_sapi(text: str) -> bytes:
    """Windows SAPI via PowerShell. Writes a WAV, then reads the PCM back."""
    out = Path(tempfile.gettempdir()) / f"synth_{abs(hash(text))}.wav"
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        # 16 kHz, 16-bit, mono -- exactly what the socket expects, so no resampling.
        "$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo("
        "16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, "
        "[System.Speech.AudioFormat.AudioChannel]::Mono); "
        f"$s.SetOutputToWaveFile('{out}', $fmt); "
        f"$s.Speak(@'\n{text}\n'@); $s.Dispose()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        check=True, capture_output=True, timeout=180,
    )
    with wave.open(str(out), "rb") as w:
        assert w.getframerate() == SAMPLE_RATE, f"expected 16k, got {w.getframerate()}"
        assert w.getnchannels() == 1, "expected mono"
        pcm = w.readframes(w.getnframes())
    out.unlink(missing_ok=True)
    return pcm


def _load_repo_env() -> None:
    """Reads the repo-root .env into the environment if present.

    The ElevenLabs key belongs here, not in any service's .env: this harness runs on the
    host and no service calls ElevenLabs. Putting it in services/voicehr/.env would ship
    a third-party credential into a container that has no use for it, and would imply
    VoiceHR depends on a second AI provider -- which it does not (docs/ARCHITECTURE_DECISIONS.md: Gemini is
    the sole provider). The repo-root .env is already gitignored (*.env).
    """
    path = Path(__file__).resolve().parents[1] / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def tts_elevenlabs(text: str) -> bytes:
    """ElevenLabs, requested as pcm_16000 so the bytes are already in the wire format."""
    _load_repo_env()
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise SystemExit(
            "ELEVENLABS_API_KEY is not set. Put it in the repo-root .env "
            "(gitignored) as ELEVENLABS_API_KEY=... , or export it."
        )
    # Default is a voice the free tier can actually use. The older "premade" ids
    # (e.g. Rachel 21m00Tcm4TlvDq8ikWAM) now count as *library* voices and return
    # 402 paid_plan_required on a free account, which is a confusing failure to hit
    # mid-interview. Override with ELEVENLABS_VOICE_ID.
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")
    model_id = os.environ.get("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
    resp = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        params={"output_format": "pcm_16000"},
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        json={"text": text, "model_id": model_id},
        timeout=180,
    )
    if resp.status_code != 200:
        raise SystemExit(f"ElevenLabs {resp.status_code}: {resp.text[:300]}")
    return resp.content


def tts_edge(text: str) -> bytes:
    """Microsoft Edge neural voices (edge-tts): free, no API key, natural quality.

    Added because SAPI's robotic voice was mis-transcribed badly enough that the
    interviewer never advanced past task 1 -- it kept issuing elaboration probes
    against garbled text ("a single batched query" came back as "a single batch
    carry"). Intelligibility is a prerequisite here, not a nicety. edge-tts emits
    MP3, so ffmpeg converts to the 16 kHz mono s16le the socket expects.
    """
    import asyncio as _asyncio

    import edge_tts

    voice = os.environ.get("EDGE_TTS_VOICE", "en-US-AriaNeural")

    async def _collect() -> bytes:
        buf = bytearray()
        async for chunk in edge_tts.Communicate(text, voice).stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        return bytes(buf)

    # Synthesis is called from inside the client's own event loop, so asyncio.run()
    # would raise "cannot be called from a running event loop". Run it on its own
    # thread, which gets its own loop.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        mp3 = pool.submit(lambda: _asyncio.run(_collect())).result()
    if not mp3:
        raise SystemExit("edge-tts returned no audio")
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-f", "s16le", "-acodec", "pcm_s16le", "-ar", str(SAMPLE_RATE), "-ac", "1", "pipe:1"],
        input=mp3, capture_output=True, timeout=180,
    )
    if proc.returncode != 0:
        raise SystemExit(f"ffmpeg failed: {proc.stderr.decode()[:300]}")
    return proc.stdout


TTS = {"sapi": tts_sapi, "edge": tts_edge, "elevenlabs": tts_elevenlabs}


# ---------------------------------------------------------------------------
# The interview client
# ---------------------------------------------------------------------------

async def speak(ws, pcm: bytes, *, realtime: bool = True) -> None:
    """Streams one utterance as 20 ms frames, paced like a real microphone.

    The pacing matters: Gemini Live runs voice-activity detection on this stream. Dumping
    a whole utterance at once looks nothing like speech and the turn may never end.
    """
    for i in range(0, len(pcm), FRAME_BYTES):
        await ws.send(pcm[i:i + FRAME_BYTES])
        if realtime:
            await asyncio.sleep(FRAME_MS / 1000)
    await ws.send(json.dumps({"type": "speech_end"}))


async def run(base_url: str, token: str, tts_name: str, *, realtime: bool,
              answer_timeout: float, nudge_after: float = 12.0,
              max_answers: int = 10) -> dict:
    tts = TTS[tts_name]
    transcript: list[dict] = []
    tasks_seen: list[dict] = []
    answered = 0

    async with httpx.AsyncClient(timeout=60) as http:
        r = await http.post(f"{base_url}/api/interview/{token}/start")
        if r.status_code == 200:
            ws_url = r.json()["ws_url"]
            print(f"  interview active; ws={ws_url}", flush=True)
        elif r.status_code == 409:
            # Already active: /start flips pending->active and is single-use, so a run
            # that died *after* start (e.g. a TTS failure) leaves the interview usable
            # but un-startable. The socket only requires status 'active', which it is.
            ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://")
            ws_url = f"{ws_url}/ws/interview/{token}"
            print(f"  interview already active (409); reconnecting ws={ws_url}", flush=True)
        else:
            raise SystemExit(f"start failed: {r.status_code} {r.text[:300]}")

    # Pre-synthesise so TTS latency never stalls the realtime socket mid-turn.
    print(f"  synthesising {len(ANSWERS)} answers via {tts_name} ...", flush=True)
    clips = [tts(a) for a in ANSWERS]
    follow_ups = [tts(f) for f in FOLLOW_UPS]
    print(f"  synthesised {(sum(len(c) for c in clips)+sum(len(f) for f in follow_ups))/2/SAMPLE_RATE:.1f}s"
          " of speech", flush=True)

    async with websockets.connect(ws_url, max_size=None, ping_interval=20) as ws:
        last_task_at = time.monotonic()
        terminated = False

        async def heartbeat():
            while not terminated:
                await asyncio.sleep(10)
                try:
                    await ws.send(json.dumps({"type": "heartbeat"}))
                except Exception:
                    return

        # The browser announces its input stream before sending PCM
        # (frontend/interview.html: startLiveAudioStream). The server currently has no
        # handler for it, but sending it keeps this client faithful to the real one.
        await ws.send(json.dumps({"type": "live_audio_start", "sample_rate": SAMPLE_RATE}))

        hb = asyncio.create_task(heartbeat())
        try:
            # A task is "owed" an answer from the moment it arrives. The first run of
            # this script waited for audio_end before ever speaking, so when Gemini Live
            # produced no audio at all the client sat silent and the server's own
            # first-activity watchdog killed the session at 33.7s. Now a pending task
            # that goes quiet for nudge_after seconds is answered anyway.
            owed = False
            ended = False
            last_event_at = time.monotonic()
            speaking: asyncio.Task | None = None
            while not terminated:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=nudge_after)
                except asyncio.TimeoutError:
                    if (owed and answered < len(clips) + 2
                            and (speaking is None or speaking.done())):
                        clip = (clips[answered] if answered < len(clips)
                                else follow_ups[(answered - len(clips)) % len(follow_ups)])
                        print(f"  -> no interviewer audio for {nudge_after:.0f}s; "
                              f"answering anyway ({len(clip)/2/SAMPLE_RATE:.1f}s)", flush=True)
                        speaking = asyncio.create_task(speak(ws, clip, realtime=realtime))
                        answered += 1
                        owed = False
                        continue
                    if time.monotonic() - last_event_at > answer_timeout:
                        print("  !! no server activity within timeout; ending", flush=True)
                        break
                    continue

                if isinstance(raw, (bytes, bytearray)):
                    last_event_at = time.monotonic()
                    continue  # interviewer audio; we do not need to play it

                last_event_at = time.monotonic()
                event = json.loads(raw)
                etype = event.get("type")

                if etype == "task":
                    tasks_seen.append(event)
                    phase = event.get("phase")
                    marker = "  [SECTION E]" if event.get("follow_up") else ""
                    print(f"  task {event.get('task')}/{event.get('total')} "
                          f"phase={phase}{marker}: {event.get('text','')[:70]}", flush=True)
                    last_task_at = time.monotonic()
                    owed = True

                elif etype == "transcript_delta":
                    transcript.append({"role": event.get("role"), "text": event.get("text")})

                elif etype == "audio_end":
                    # The interviewer finished speaking -> our turn. Speak in a task so
                    # the receive loop keeps draining while we stream ~20-35s of audio.
                    if speaking is None or speaking.done():
                        clip = (clips[answered] if answered < len(clips)
                                else follow_ups[(answered - len(clips)) % len(follow_ups)])
                        label = (f"answer {answered+1}/{len(clips)}" if answered < len(clips)
                                 else f"follow-up {(answered - len(clips)) % len(follow_ups) + 1}")
                        print(f"  -> speaking {label} ({len(clip)/2/SAMPLE_RATE:.1f}s)", flush=True)
                        speaking = asyncio.create_task(speak(ws, clip, realtime=realtime))
                        answered += 1
                        owed = False

                if (max_answers and answered >= max_answers
                        and (speaking is None or speaking.done()) and not ended):
                    print(f"  -> {answered} answers given; ending the interview cleanly",
                          flush=True)
                    await ws.send(json.dumps({"type": "end"}))
                    ended = True

                elif etype in ("conversation_terminated", "live_error", "already_active"):
                    print(f"  << {etype}: {event.get('message','')}", flush=True)
                    terminated = True

                elif etype in ("ready", "live_connected", "audio_start"):
                    print(f"  << {etype}", flush=True)
        finally:
            terminated = True
            hb.cancel()
            if speaking is not None and not speaking.done():
                speaking.cancel()

    return {"transcript": transcript, "tasks": tasks_seen, "answered": answered}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8004")
    ap.add_argument("--token", help="existing interview token; otherwise use --invite-*")
    ap.add_argument("--tts", choices=sorted(TTS), default="edge")
    ap.add_argument("--answer-timeout", type=float, default=120.0)
    ap.add_argument("--max-answers", type=int, default=10,
                    help="end the interview cleanly after this many candidate answers")
    ap.add_argument("--nudge-after", type=float, default=12.0,
                    help="answer a pending task after this many silent seconds")
    ap.add_argument("--fast", action="store_true",
                    help="do not pace audio in realtime (will likely break Live VAD)")
    args = ap.parse_args()

    if not args.token:
        raise SystemExit("--token is required (create one via RWS invite, see handoff)")

    print(f"\nSynthetic interview: token={args.token[:8]}... tts={args.tts}\n")
    result = asyncio.run(run(
        args.base_url, args.token, args.tts,
        realtime=not args.fast, answer_timeout=args.answer_timeout,
        nudge_after=args.nudge_after, max_answers=args.max_answers,
    ))

    print(f"\n  answers spoken : {result['answered']}")
    print(f"  tasks received : {len(result['tasks'])}")
    section_e = [t for t in result["tasks"] if t.get("follow_up")]
    print(f"  Section E fired: {bool(section_e)}"
          + (f"  (phase={section_e[0].get('phase')})" if section_e else ""))
    print(f"  transcript turns: {len(result['transcript'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

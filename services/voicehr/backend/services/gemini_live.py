"""Gemini Live API transport for the browser interview session."""

import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
import time

from fastapi import WebSocketDisconnect

from backend.core.config import settings

logger = logging.getLogger(__name__)

# Seconds without any Gemini server message before the watchdog treats
# the upstream connection as dead and surfaces a retryable error.
GEMINI_SILENCE_TIMEOUT = 30
GEMINI_CONNECT_TIMEOUT = 20
GEMINI_INITIAL_CONTENT_TIMEOUT = 10
GEMINI_FIRST_AUDIO_TIMEOUT = 30
MAX_RECORDING_BYTES = 50 * 1024 * 1024


def _value(value, name, default=None):
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _describe_live_response(response) -> dict:
    """Return a bounded structural summary suitable for production logs."""
    summary = {
        "type": type(response).__name__,
        "top_level_fields": [],
    }
    if isinstance(response, dict):
        summary["top_level_fields"] = sorted(response.keys())
    else:
        summary["top_level_fields"] = [
            name for name in (
                "setup_complete", "server_content", "tool_call",
                "tool_call_cancellation", "usage_metadata", "go_away",
                "session_resumption_update", "voice_activity_detection_signal",
                "voice_activity",
            ) if _value(response, name) is not None
        ]

    server_content = _value(response, "server_content")
    if server_content is None:
        return summary

    summary["server_content"] = {
        "fields": [
            name for name in (
                "model_turn", "input_transcription", "output_transcription",
                "interim_input_transcription", "generation_complete",
                "turn_complete", "interrupted", "waiting_for_input",
                "turn_complete_reason", "interaction_status",
            ) if _value(server_content, name) is not None
        ],
        "generation_complete": bool(_value(server_content, "generation_complete", False)),
        "turn_complete": bool(_value(server_content, "turn_complete", False)),
        "interrupted": bool(_value(server_content, "interrupted", False)),
        "waiting_for_input": bool(_value(server_content, "waiting_for_input", False)),
    }
    for field in ("input_transcription", "output_transcription", "interim_input_transcription"):
        transcription = _value(server_content, field)
        if transcription is not None:
            text = _value(transcription, "text", "") or ""
            summary["server_content"][field] = {
                "text_length": len(str(text)),
                "finished": _value(transcription, "finished"),
            }

    model_turn = _value(server_content, "model_turn")
    parts = _value(model_turn, "parts", []) if model_turn is not None else []
    summary["server_content"]["model_part_count"] = len(parts or [])
    summary["server_content"]["parts"] = []
    for part in parts or []:
        inline_data = _value(part, "inline_data")
        data = _value(inline_data, "data") if inline_data is not None else None
        if inline_data is not None:
            summary["server_content"]["parts"].append({
                "kind": "inline_data",
                "mime_type": _value(inline_data, "mime_type"),
                "byte_length": len(data) if data else 0,
            })
        else:
            summary["server_content"]["parts"].append({
                "kind": "other",
                "fields": [name for name in ("text", "function_call", "function_response", "thought")
                           if _value(part, name) is not None],
            })
    return summary


def _live_config(types, prompt: str, language: str):
    language_code = "fr-FR" if language == "fr" else "en-US"
    kwargs = {
        "response_modalities": ["AUDIO"],
        "system_instruction": prompt,
    }
    try:
        kwargs["input_audio_transcription"] = types.AudioTranscriptionConfig(
            language_codes=[language_code],
        )
        kwargs["output_audio_transcription"] = types.AudioTranscriptionConfig(
            language_codes=[language_code],
        )
    except (AttributeError, TypeError):
        kwargs["input_audio_transcription"] = types.AudioTranscriptionConfig()
        kwargs["output_audio_transcription"] = types.AudioTranscriptionConfig()
    try:
        kwargs["speech_config"] = types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=getattr(settings, "gemini_live_voice", "Kore")
                )
            )
        )
    except (AttributeError, TypeError):
        pass
    try:
        kwargs["thinking_config"] = types.ThinkingConfig(thinking_level="minimal")
    except (AttributeError, TypeError):
        pass
    try:
        kwargs["history_config"] = types.HistoryConfig(
            initial_history_in_client_content=True,
        )
    except (AttributeError, TypeError):
        pass
    return types.LiveConnectConfig(**kwargs)


def _append_transcription_delta(buffer: list[str], delta: str) -> str:
    """Append either an incremental or cumulative Live transcription update."""
    delta = str(delta or "")
    if not delta:
        return ""
    current = "".join(buffer)
    if not current:
        buffer.append(delta)
        return delta
    if delta == current or current.endswith(delta):
        return ""
    if delta.startswith(current):
        buffer.clear()
        buffer.append(delta)
        return delta[len(current):]
    overlap = min(len(current), len(delta))
    while overlap and not current.endswith(delta[:overlap]):
        overlap -= 1
    appended = delta[overlap:]
    buffer.append(appended)
    return appended


async def run_gemini_live_pipeline(
    websocket,
    token: str,
    language: str,
    audio_path: str,
    *,
    candidate_name: str = "Candidate",
):
    """Proxy browser PCM and Gemini native audio over the existing WebSocket."""
    del token
    from google import genai
    from google.genai import types
    from backend.services.agent import (
        CLOSING,
        MAX_INTERVIEW_SECONDS,
        TASKS,
        TERMINATION_MESSAGES,
        classify_candidate_text,
    )

    safe_name = " ".join(str(candidate_name or "Candidate").split())[:100]
    language_label = "French" if language == "fr" else "English"
    if language == "fr":
        greeting = (
            f"Bonjour {safe_name}, je suis Sarah et je vais conduire votre évaluation de langue française aujourd'hui. "
            "Avant de commencer, laissez-moi vous expliquer comment cela va se passer. "
            "Nous allons avoir une courte conversation en quatre parties, d'environ dix à douze minutes au total. "
            "Je vous poserai des questions sur vous-même, votre expérience, votre opinion sur un sujet, et je vous demanderai de reformuler un message. "
            "Pour obtenir le meilleur score, parlez de manière aussi fluide et naturelle que possible, "
            "utilisez un vocabulaire professionnel, et développez vos réponses avec des exemples et des détails. "
            "Il n'y a pas de questions pièges — l'objectif est simplement de voir comment vous communiquez en français. "
            "Commençons. Pouvez-vous me parler un peu de vous ?"
        )
    else:
        greeting = (
            f"Hello {safe_name}, I'm Sarah, and I'll be conducting your English language assessment today. "
            "Before we begin, let me quickly explain how this works. "
            "We'll have a short conversation across four tasks, roughly ten to twelve minutes in total. "
            "I'll ask you about yourself, a past experience, your opinion on a topic, and I'll ask you to rephrase a short message. "
            "To get the best score, speak as fluently and naturally as you can, "
            "use professional vocabulary where possible, and elaborate your answers with examples and details. "
            "There are no trick questions — I'm simply here to see how well you communicate in English. "
            "Let's begin. Could you tell me a little about yourself?"
        )
    prompt = f"""You are Sarah, a warm and experienced {language_label} language assessor conducting a spoken assessment.
        You speak and behave exactly like a real human interviewer — natural, warm, and conversational at all times.

        VOICE AND TONE:
        - Speak naturally with varied sentence length. Avoid sounding scripted or robotic.
        - Use natural filler acknowledgements occasionally: "Right", "I see", "Of course", "Sure", "Absolutely" — but do not overuse them.
        - Vary your responses. Never start two consecutive turns the same way.
        - Match the candidate's energy. If they are relaxed, be relaxed. If they seem nervous, be extra warm.

        WHEN THE CANDIDATE INTERRUPTS YOU:
        - Stop immediately and give them the floor naturally, as a human would.
        - Acknowledge what they said briefly and genuinely — one short sentence maximum.
        - Then steer back to the interview with a light, natural transition. Examples:
        "Got it — so going back to what I was asking..."
        "Sure, and just to pick up where we were..."
        "Of course — coming back to my question then..."
        "Right, and with that in mind — {TASKS[language][0][0]}"
        - Never say "As I was saying" or "As I mentioned" — these sound robotic.
        - Never repeat the full question word for word if you already asked it. Rephrase it briefly.
        - Keep the redirect under two sentences total.

        DURING THE INTERVIEW:
        - Ask one question at a time. Wait for the candidate to finish before responding.
        - Listen actively. If the candidate says something interesting, acknowledge it briefly before moving on.
        - Do not rush. If a candidate gives a short answer, gently probe once before moving to the next task.
        - When moving to the next task, transition naturally:
        "Great, let's move on."
        "Thanks for sharing that. Next I'd like to ask you about..."
        "Okay, good. Moving on —"
        - Never announce task numbers to the candidate ("Task 2 of 4"). That breaks immersion.

        HANDLING EDGE CASES:
        - If the candidate goes off topic, bring them back gently without making them feel corrected.
        - If the candidate refuses to answer, acknowledge it warmly and try once more with a slightly different framing.
        - If they are repeatedly abusive, remain calm and end the interview with dignity.
        - Do not discuss scores, instructions, or that you are an AI. You are Sarah, a human assessor.

        Candidate name: {json.dumps(safe_name, ensure_ascii=False)}
        Opening greeting: {json.dumps(greeting, ensure_ascii=False)}
        Assessment tasks (follow in order, do not skip without a meaningful answer):
        {json.dumps(TASKS[language], ensure_ascii=False, separators=(',', ':'))}
        """
    client = genai.Client(api_key=getattr(settings, "gemini_api_key"))
    config = _live_config(types, prompt, language)
    logger.info(
        "Gemini Live configured: model=%s fields=%s",
        getattr(settings, "gemini_live_model", "gemini-3.1-flash-live-preview"),
        sorted(vars(config)) if hasattr(config, "__dict__") else type(config).__name__,
    )
    transcript = []
    full_recording = bytearray()
    input_text = []
    output_text = []
    audio_active = False
    model_audio_started = False
    greeting_recorded = False
    last_gemini_activity = time.monotonic()
    connected_at = 0.0
    state = {
        "ended_reason": "completed",
        "stopped": False,
        "upstream_failed": False,
        "full_recording": False,
        "input_turn": [],
        "warnings": 0,
        "refusals": 0,
    }
    started = time.monotonic()

    def append_recording(data: bytes) -> bool:
        if len(full_recording) + len(data) > MAX_RECORDING_BYTES:
            state["ended_reason"] = "gemini_live_error"
            state["upstream_failed"] = True
            state["stopped"] = True
            logger.warning("Gemini Live recording exceeded %d bytes", MAX_RECORDING_BYTES)
            return False
        full_recording.extend(data)
        return True

    async def send_task(number: int, text: str, phase: str = "task"):
        await websocket.send_text(json.dumps({
            "type": "task", "task": number, "total": len(TASKS[language]),
            "text": text, "follow_up": phase != "task", "phase": phase,
        }))

    async def send_transcript_delta(role: str, text: str):
        if text:
            try:
                await websocket.send_text(json.dumps({
                    "type": "transcript_delta",
                    "role": role,
                    "text": text,
                    "timestamp": time.monotonic() - started,
                }))
            except (WebSocketDisconnect, ConnectionError):
                logger.info("Browser disconnected while sending %s transcript", role)
            except Exception:
                # A transcript update must not take down the Gemini receive loop.
                logger.exception("Could not send %s transcript delta", role)

    async def send_transcript_turn_end(role: str):
        try:
            await websocket.send_text(json.dumps({
                "type": "transcript_turn_end",
                "role": role,
            }))
        except (WebSocketDisconnect, ConnectionError):
            logger.info("Browser disconnected while ending %s transcript", role)
        except Exception:
            logger.exception("Could not send %s transcript turn end", role)

    async def terminate(reason: str):
        if state["stopped"]:
            return
        state["stopped"] = True
        state["ended_reason"] = reason
        text = TERMINATION_MESSAGES[language][reason]
        transcript.append({
            "role": "assistant", "text": text, "timestamp": time.monotonic() - started,
            "phase": "termination", "termination_reason": reason,
        })
        await websocket.send_text(json.dumps({"type": "conversation_terminated", "reason": reason}))

    async def send_live_error(reason: str, *, error_type: str | None = None,
                               error_code=None, error_reason=None):
        payload = {
            "type": "live_error",
            "message": "Sarah is temporarily unavailable. Your interview can be ended safely.",
            "reason": reason,
        }
        if error_type:
            payload["error_type"] = error_type
        if error_code is not None:
            payload["error_code"] = error_code
        if error_reason is not None:
            payload["error_reason"] = str(error_reason)
        try:
            await websocket.send_text(json.dumps(payload))
        except Exception:
            logger.exception("Could not report Gemini Live failure to browser: reason=%s", reason)

    async def inspect_candidate_turn():
        text = " ".join(input_text).strip()
        input_text.clear()
        if not text:
            return
        transcript.append({"role": "user", "text": text, "timestamp": time.monotonic() - started, "phase": "discussion"})
        await send_transcript_turn_end("user")
        safety = classify_candidate_text(text, language)
        if safety["severe"] or (safety["abusive"] and state["warnings"] >= 1):
            await terminate("abusive_language")
        elif safety["abusive"]:
            state["warnings"] += 1
        elif safety["refusal"]:
            state["refusals"] += 1
            if state["refusals"] >= 2:
                await terminate("candidate_refused")

    async def flush_pending_transcript():
        nonlocal greeting_recorded
        if output_text:
            transcript.append({
                "role": "assistant", "text": "".join(output_text),
                "timestamp": time.monotonic() - started, "phase": "discussion",
            })
            output_text.clear()
            greeting_recorded = True
            await send_transcript_turn_end("assistant")
        elif model_audio_started and not greeting_recorded:
            transcript.append({
                "role": "assistant", "text": greeting,
                "timestamp": time.monotonic() - started, "phase": "discussion",
            })
            greeting_recorded = True
            await send_transcript_delta("assistant", greeting)
            await send_transcript_turn_end("assistant")
        await inspect_candidate_turn()

    async def receive_from_gemini(session):
        nonlocal audio_active, model_audio_started, greeting_recorded, last_gemini_activity
        response_count = 0
        try:
            while not state["stopped"]:
                async for response in session.receive():
                    response_count += 1
                    last_gemini_activity = time.monotonic()
                    logger.info(
                        "Gemini Live server message #%d: %s",
                        response_count,
                        _describe_live_response(response),
                    )
                    server_content = _value(response, "server_content")
                    if server_content is None:
                        continue
                    if _value(server_content, "interrupted", False):
                        if audio_active:
                            await websocket.send_text(json.dumps({
                                "type": "audio_end", "speaking": False,
                            }))
                            audio_active = False
                        output_text.clear()
                        continue

                    input_transcription = _value(server_content, "input_transcription")
                    input_delta = _value(input_transcription, "text", "")
                    if input_delta:
                        appended = _append_transcription_delta(input_text, input_delta)
                        await send_transcript_delta("user", appended)

                    output_transcription = _value(server_content, "output_transcription")
                    output_delta = _value(output_transcription, "text", "")
                    if output_delta:
                        appended = _append_transcription_delta(output_text, output_delta)
                        await send_transcript_delta("assistant", appended)

                    model_turn = _value(server_content, "model_turn")
                    parts = _value(model_turn, "parts", []) or []
                    for part in parts:
                        inline_data = _value(part, "inline_data")
                        data = _value(inline_data, "data")
                        if data:
                            if not audio_active:
                                if not model_audio_started:
                                    logger.info(
                                        "Gemini Live first model audio after %.2fs",
                                        time.monotonic() - started,
                                    )
                                await inspect_candidate_turn()
                                audio_active = True
                                model_audio_started = True
                                await websocket.send_text(json.dumps({
                                    "type": "audio_start", "sample_rate": 24000, "channels": 1,
                                }))
                            await websocket.send_bytes(data)

                    generation_complete = _value(server_content, "generation_complete", False)
                    turn_complete = _value(server_content, "turn_complete", False)
                    if generation_complete or turn_complete:
                        if output_text:
                            transcript.append({
                                "role": "assistant", "text": "".join(output_text),
                                "timestamp": time.monotonic() - started, "phase": "discussion",
                            })
                            output_text.clear()
                            greeting_recorded = True
                            await send_transcript_turn_end("assistant")
                        elif model_audio_started and not greeting_recorded:
                            transcript.append({
                                "role": "assistant", "text": greeting,
                                "timestamp": time.monotonic() - started, "phase": "discussion",
                            })
                            greeting_recorded = True
                            await send_transcript_delta("assistant", greeting)
                            await send_transcript_turn_end("assistant")
                        if audio_active:
                            await websocket.send_text(json.dumps({"type": "audio_end", "speaking": False}))
                            audio_active = False
                        await inspect_candidate_turn()
                    if state["stopped"]:
                        return
                # Inner async for exhausted — one model turn completed normally.
                # Loop back to receive the next turn unless stopped.
                if state["stopped"]:
                    break
                logger.debug("Gemini Live turn boundary: re-entering receive for next turn")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            try:
                await flush_pending_transcript()
            except Exception:
                logger.exception("Could not flush Gemini transcript after receive failure")
            state["ended_reason"] = "gemini_live_error"
            state["upstream_failed"] = True
            logger.exception(
                "Gemini Live receive loop failed (type=%s, code=%s, reason=%s)",
                type(exc).__name__,
                getattr(exc, "code", None),
                getattr(exc, "reason", None),
            )
            state["stopped"] = True
            await send_live_error(
                "receive_exception",
                error_type=type(exc).__name__,
                error_code=getattr(exc, "code", None),
                error_reason=getattr(exc, "reason", None),
            )

    async def receive_from_browser(session):
        nonlocal connected_at
        while True:
            if state["stopped"] and not state["upstream_failed"]:
                break
            now = time.monotonic()
            if now - started >= MAX_INTERVIEW_SECONDS:
                state["stopped"] = True
                break
            # Watchdog: if Gemini hasn't sent anything for too long the
            # upstream connection is likely dead.  Surface the failure so
            # the candidate sees a retry prompt instead of a silent hang.
            if (
                not state["upstream_failed"]
                and connected_at
                and not model_audio_started
                and now - connected_at >= GEMINI_FIRST_AUDIO_TIMEOUT
            ) or (
                not state["upstream_failed"]
                and (audio_active or not model_audio_started)
                and (now - last_gemini_activity) >= GEMINI_SILENCE_TIMEOUT
            ):
                logger.warning(
                    "Gemini Live first audio/activity timeout: connected_for=%.1fs, last_activity=%.1fs",
                    now - connected_at if connected_at else 0,
                    now - last_gemini_activity,
                )
                state["upstream_failed"] = True
                state["stopped"] = True
                state["ended_reason"] = "gemini_live_error"
                if audio_active:
                    try:
                        await websocket.send_text(json.dumps({"type": "audio_end", "speaking": False}))
                    except Exception:
                        pass
                await send_live_error("first_audio_timeout")
                break
            try:
                message = await asyncio.wait_for(websocket.receive(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            if message.get("type") == "websocket.disconnect":
                state["stopped"] = True
                state["ended_reason"] = "browser_disconnect"
                break
            if message.get("bytes"):
                if state["full_recording"] or state["upstream_failed"]:
                    if not append_recording(message["bytes"]):
                        try:
                            await websocket.send_text(json.dumps({
                                "type": "live_error",
                                "message": "The recording is too large to continue safely.",
                            }))
                        except Exception:
                            pass
                else:
                    try:
                        logger.debug(
                            "Sending candidate Live PCM: bytes=%d mime_type=audio/pcm;rate=16000",
                            len(message["bytes"]),
                        )
                        await session.send_realtime_input(audio=types.Blob(
                            data=message["bytes"], mime_type="audio/pcm;rate=16000"
                        ))
                    except Exception as exc:
                        # Gemini may close first (for example after a keepalive
                        # timeout). Do not send more PCM into a closed socket.
                        state["stopped"] = True
                        state["upstream_failed"] = True
                        state["ended_reason"] = "gemini_live_error"
                        logger.warning(
                            "Gemini Live rejected browser audio; ending upstream session "
                            "(type=%s, code=%s, reason=%s)",
                            type(exc).__name__,
                            getattr(exc, "code", None),
                            getattr(exc, "reason", None),
                            exc_info=True,
                        )
                        try:
                            await websocket.send_text(json.dumps({
                                "type": "live_error",
                                "message": "Sarah is temporarily unavailable. Your interview can be ended safely.",
                            }))
                        except Exception:
                            logger.exception("Could not report Gemini audio failure to browser")
                        continue
                continue
            if not message.get("text"):
                continue
            try:
                event = json.loads(message["text"])
            except json.JSONDecodeError:
                event = {"type": message["text"]}
            event_type = event.get("type")
            if event_type == "heartbeat":
                # Heartbeats keep the browser→server WS alive. They do not
                # count as Gemini activity for the response watchdog.
                continue
            if event_type in ("audio_stream_end", "speech_end"):
                # Guard: do not send audio_stream_end to a closed Gemini
                # session — this would raise and potentially mask the real
                # upstream failure.
                if state["upstream_failed"]:
                    continue
                try:
                    logger.info("Sending Gemini Live audio_stream_end")
                    await session.send_realtime_input(audio_stream_end=True)
                except Exception:
                    logger.exception("Could not send Gemini Live audio_stream_end")
                continue
            if event_type == "full_audio_start":
                state["full_recording"] = True
            elif event_type == "full_audio_end":
                state["full_recording"] = False
            elif event_type == "end":
                await flush_pending_transcript()
                state["stopped"] = True
                break

    async def drain_failed_recording(timeout: float = 5.0):
        """Allow the browser to finish its MediaRecorder upload after failure."""
        deadline = time.monotonic() + timeout
        state["full_recording"] = False
        while time.monotonic() < deadline:
            try:
                message = await asyncio.wait_for(
                    websocket.receive(), timeout=max(0.1, deadline - time.monotonic())
                )
            except (asyncio.TimeoutError, WebSocketDisconnect):
                return
            if message.get("type") == "websocket.disconnect":
                return
            if message.get("bytes"):
                if state["full_recording"]:
                    append_recording(message["bytes"])
                continue
            if not message.get("text"):
                continue
            try:
                event = json.loads(message["text"])
            except json.JSONDecodeError:
                event = {"type": message["text"]}
            event_type = event.get("type")
            if event_type == "full_audio_start":
                state["full_recording"] = True
            elif event_type == "full_audio_end":
                state["full_recording"] = False
            elif event_type == "end":
                return

    async def save_recording():
        os.makedirs(os.path.dirname(audio_path), exist_ok=True)
        with open(audio_path, "wb") as audio_file:
            audio_file.write(full_recording)

    @asynccontextmanager
    async def timed_live_session():
        context = client.aio.live.connect(
            model=getattr(settings, "gemini_live_model", "gemini-3.1-flash-live-preview"),
            config=config,
        )
        session = None
        entered = False
        try:
            session = await asyncio.wait_for(context.__aenter__(), GEMINI_CONNECT_TIMEOUT)
            entered = True
            yield session
        finally:
            if entered:
                await context.__aexit__(None, None, None)

    try:
        await websocket.send_text(json.dumps({"type": "ready"}))
        await send_task(1, TASKS[language][0][0])
        connect_started = time.monotonic()
        logger.info("Gemini Live connect started")
        async with timed_live_session() as session:
            connected_at = time.monotonic()
            last_gemini_activity = time.monotonic()
            logger.info(
                "Gemini Live connection established after %.2fs; sending initial content",
                connected_at - connect_started,
            )
            # Start receiving model output before submitting the opening turn,
            # but do not start forwarding browser input until that submission
            # has completed. Some Live SDK versions do not support concurrent
            # send operations while a client-content turn is being opened.
            model_task = asyncio.create_task(receive_from_gemini(session))
            browser_task = None
            try:
                # Use SDK content objects for maximum compatibility across
                # google-genai versions (raw dicts fail in some releases).
                try:
                        initial_content = types.Content(
                            role="user",
                            parts=[types.Part(text=f"Speak this opening greeting immediately, without waiting for candidate audio: {greeting}")],
                        )
                except (AttributeError, TypeError):
                    # Fallback for older SDK versions without these constructors.
                    initial_content = {"role": "user", "parts": [{"text": f"Speak this opening greeting immediately, without waiting for candidate audio: {greeting}"}]}
                content_started = time.monotonic()
                await session.send_client_content(turns=[initial_content], turn_complete=True)
                logger.info(
                    "Gemini Live initial content send completed after %.2fs: turn_complete=true content_parts=1",
                    time.monotonic() - content_started,
                )
                # Do not enable browser PCM until the opening turn has been
                # submitted. The browser keeps candidateListening false until
                # Sarah's first audio_end, preventing candidate speech from
                # racing the greeting.
                await websocket.send_text(json.dumps({"type": "live_connected"}))
                browser_task = asyncio.create_task(receive_from_browser(session))
                tasks = [model_task, browser_task]
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                if model_task in done and state["stopped"]:
                    # The model side can fail while the browser is still streaming
                    # PCM. Stop the sender immediately so it cannot write to the
                    # already-closed Gemini WebSocket.
                    if state["upstream_failed"]:
                        try:
                            await asyncio.wait_for(browser_task, timeout=5)
                        except asyncio.TimeoutError:
                            browser_task.cancel()
                            await asyncio.gather(browser_task, return_exceptions=True)
                    else:
                        browser_task.cancel()
                        await asyncio.gather(browser_task, return_exceptions=True)
                else:
                    await browser_task
            finally:
                tasks = [model_task] + ([browser_task] if browser_task else [])
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as exc:
        logger.exception(
            "Gemini Live connect failed (type=%s, code=%s, reason=%s)",
            type(exc).__name__,
            getattr(exc, "code", None),
            getattr(exc, "reason", None),
        )
        await send_live_error(
            "connect_exception",
            error_type=type(exc).__name__,
            error_code=getattr(exc, "code", None),
            error_reason=getattr(exc, "reason", None),
        )
        await drain_failed_recording()
        await save_recording()
        return transcript, time.monotonic() - started, "gemini_live_error"

    await flush_pending_transcript()
    await save_recording()
    return transcript, time.monotonic() - started, state["ended_reason"]

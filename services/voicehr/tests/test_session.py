import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.conftest import create_interview


class FakeSocket:
    def __init__(self):
        self.text = []
        self.binary = []

    async def send_text(self, value):
        self.text.append(value)

    async def send_bytes(self, value):
        self.binary.append(value)


def test_candidate_safety_classification_distinguishes_refusal_and_severe_abuse():
    from backend.services.agent import classify_candidate_text

    assert classify_candidate_text("I refuse to answer this question", "en")["refusal"]
    assert classify_candidate_text("I will hurt you", "en")["severe"]
    assert not classify_candidate_text("I faced a difficult challenge at work", "en")["abusive"]


def test_gemini_live_proxy_streams_audio_and_transcript(tmp_path):
    from backend.services import gemini_live
    events = []

    class FakeLiveSession:
        def __init__(self):
            self.audio = []
            self.client_content = []

        async def send_realtime_input(self, **kwargs):
            self.audio.append(kwargs)

        async def send_client_content(self, **kwargs):
            self.client_content.append(kwargs)
            events.append("initial_content")

        async def receive(self):
            yield SimpleNamespace(server_content=SimpleNamespace(
                 input_transcription=SimpleNamespace(text="I manage a product team."),
                 output_transcription=SimpleNamespace(text="Hello, Alex Martin. I'm Sarah, and I'll be conducting your assessment."),
                 model_turn=SimpleNamespace(parts=[
                     SimpleNamespace(inline_data=SimpleNamespace(
                         data=b"sarah-pcm",
                         mime_type="audio/pcm;rate=24000",
                     )),
                 ]),
                 turn_complete=True,
             ))
            await asyncio.sleep(10)

    class FakeLiveContext:
        def __init__(self, session):
            self.session = session

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, *args):
            return None

    class LiveSocket(FakeSocket):
        def __init__(self):
            super().__init__()
            self.messages = [
                {"text": '{"type":"live_audio_start","sample_rate":16000}'},
                {"text": '{"type":"heartbeat"}'},
                {"bytes": b"pcm-audio"},
                {"text": '{"type":"audio_stream_end"}'},
                {"text": '{"type":"full_audio_start"}'},
                {"bytes": b"webm-audio"},
                {"text": '{"type":"full_audio_end"}'},
                {"text": '{"type":"end"}'},
            ]

        async def send_text(self, value):
            super_result = await super().send_text(value)
            if json.loads(value).get("type") == "live_connected":
                events.append("live_connected")
            return super_result

        async def receive(self):
            return self.messages.pop(0)

    session = FakeLiveSession()
    client = SimpleNamespace(aio=SimpleNamespace(live=SimpleNamespace(
        connect=lambda **kwargs: FakeLiveContext(session)
    )))
    socket = LiveSocket()
    audio_path = str(tmp_path / "live.webm")
    with patch.object(gemini_live, "settings", SimpleNamespace(
        gemini_api_key="test-key",
        gemini_live_model="test-live-model",
        gemini_live_voice="Kore",
    )), patch("google.genai.Client", return_value=client):
        transcript, _, ended_reason = asyncio.run(gemini_live.run_gemini_live_pipeline(
            socket, "token", "en", audio_path, candidate_name="Alex Martin",
        ))

    assert ended_reason == "completed"
    audio_inputs = [item for item in session.audio if "audio" in item]
    assert audio_inputs[0]["audio"].data == b"pcm-audio"
    assert any("audio_stream_end" in item for item in session.audio)
    assert socket.binary[0] == b"sarah-pcm"
    assert any('"type": "audio_start"' in message for message in socket.text)
    assert any('"type": "audio_end"' in message for message in socket.text)
    assert any(turn["role"] == "user" for turn in transcript)
    assert any(turn["role"] == "assistant" for turn in transcript)
    assert "Alex Martin" in repr(session.client_content)
    assert "Alex Martin" in next(turn["text"] for turn in transcript if turn["role"] == "assistant")
    live_transcript = [json.loads(message) for message in socket.text if message.startswith('{')]
    content_index = next(
        index for index, item in enumerate(socket.text)
        if item.startswith('{') and json.loads(item).get("type") == "live_connected"
    )
    assert session.client_content
    assert content_index > 0
    assert events.index("initial_content") < events.index("live_connected")
    assert any(message.get("type") == "transcript_delta" and message.get("role") == "user" for message in live_transcript)
    assert any(message.get("type") == "transcript_turn_end" and message.get("role") == "user" for message in live_transcript)


def test_gemini_live_transcription_config_uses_interview_language():
    from backend.services.gemini_live import _append_transcription_delta, _live_config
    from google.genai import types

    config = _live_config(types, "prompt", "fr")
    assert config.input_audio_transcription.language_codes == ["fr-FR"]
    assert config.output_audio_transcription.language_codes == ["fr-FR"]
    assert config.thinking_config.thinking_level.value == "MINIMAL"
    # Deliberately unset: initial_history_in_client_content makes
    # gemini-3.1-flash-live-preview emit no audio at all (bisected -- see
    # gemini_live.py: _live_config). This assertion guards the fix, not a preference.
    assert config.history_config is None

    buffer = []
    _append_transcription_delta(buffer, "I manage")
    _append_transcription_delta(buffer, "I manage a team")
    _append_transcription_delta(buffer, " and projects")
    assert "".join(buffer) == "I manage a team and projects"


def test_gemini_live_upstream_failure_preserves_recording_and_is_retryable(tmp_path):
    from backend.services import gemini_live

    class FailingLiveSession:
        async def send_realtime_input(self, **kwargs):
            raise ConnectionError("sent 1011 keepalive ping timeout")

        async def send_client_content(self, **kwargs):
            return None

        async def receive(self):
            await asyncio.sleep(10)
            if False:
                yield None

    class FakeLiveContext:
        def __init__(self, session):
            self.session = session

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, *args):
            return None

    class LiveSocket(FakeSocket):
        def __init__(self):
            super().__init__()
            self.messages = [
                {"text": '{"type":"live_audio_start"}'},
                {"bytes": b"pcm-audio"},
                {"text": '{"type":"full_audio_start"}'},
                {"bytes": b"webm-audio"},
                {"text": '{"type":"full_audio_end"}'},
                {"text": '{"type":"end"}'},
            ]

        async def receive(self):
            return self.messages.pop(0)

    session = FailingLiveSession()
    client = SimpleNamespace(aio=SimpleNamespace(live=SimpleNamespace(
        connect=lambda **kwargs: FakeLiveContext(session)
    )))
    socket = LiveSocket()
    audio_path = str(tmp_path / "failed-live.webm")
    with patch.object(gemini_live, "settings", SimpleNamespace(
        gemini_api_key="test-key",
        gemini_live_model="test-live-model",
        gemini_live_voice="Kore",
    )), patch("google.genai.Client", return_value=client):
        transcript, _, ended_reason = asyncio.run(gemini_live.run_gemini_live_pipeline(
            socket, "token", "en", audio_path,
        ))

    assert ended_reason == "gemini_live_error"
    with open(audio_path, "rb") as audio_file:
        assert audio_file.read() == b"webm-audio"
    assert any('"type": "live_error"' in message for message in socket.text)


def test_gemini_live_handshake_failure_drains_recording(tmp_path):
    from backend.services import gemini_live

    class FailingLiveContext:
        async def __aenter__(self):
            raise RuntimeError("3.1 handshake rejected")

        async def __aexit__(self, *args):
            return None

    class LiveSocket(FakeSocket):
        def __init__(self):
            super().__init__()
            self.messages = [
                {"text": '{"type":"full_audio_start"}'},
                {"bytes": b"handshake-failure-recording"},
                {"text": '{"type":"full_audio_end"}'},
                {"text": '{"type":"end"}'},
            ]

        async def receive(self):
            return self.messages.pop(0)

    client = SimpleNamespace(aio=SimpleNamespace(live=SimpleNamespace(
        connect=lambda **kwargs: FailingLiveContext()
    )))
    socket = LiveSocket()
    audio_path = str(tmp_path / "handshake-failed-live.webm")
    with patch.object(gemini_live, "settings", SimpleNamespace(
        gemini_api_key="test-key",
        gemini_live_model="test-live-model",
        gemini_live_voice="Kore",
    )), patch("google.genai.Client", return_value=client):
        transcript, _, ended_reason = asyncio.run(gemini_live.run_gemini_live_pipeline(
            socket, "token", "en", audio_path,
        ))

    assert transcript == []
    assert ended_reason == "gemini_live_error"
    with open(audio_path, "rb") as audio_file:
        assert audio_file.read() == b"handshake-failure-recording"
    assert any('"type": "live_error"' in message for message in socket.text)


def test_gemini_live_interrupted_resets_audio_state(tmp_path):
    """Verify that server_content.interrupted clears audio_active and output text."""
    from backend.services import gemini_live

    class InterruptedLiveSession:
        def __init__(self):
            self.audio = []
            self.client_content = []

        async def send_realtime_input(self, **kwargs):
            self.audio.append(kwargs)

        async def send_client_content(self, **kwargs):
            self.client_content.append(kwargs)

        async def receive(self):
            # First: model starts speaking (audio data)
            yield SimpleNamespace(server_content=SimpleNamespace(
                input_transcription=None,
                output_transcription=SimpleNamespace(text="Hello, let me "),
                model_turn=SimpleNamespace(parts=[
                    SimpleNamespace(inline_data=SimpleNamespace(data=b"\x00\x01")),
                ]),
                turn_complete=False,
                generation_complete=False,
                interrupted=False,
            ))
            # Second: interrupted by candidate barge-in
            yield SimpleNamespace(server_content=SimpleNamespace(
                input_transcription=None,
                output_transcription=None,
                model_turn=None,
                turn_complete=False,
                generation_complete=False,
                interrupted=True,
            ))
            # Third: new response after interruption (turn completes)
            yield SimpleNamespace(server_content=SimpleNamespace(
                input_transcription=SimpleNamespace(text="I have a question."),
                output_transcription=SimpleNamespace(text="Of course."),
                model_turn=SimpleNamespace(parts=[]),
                turn_complete=True,
                generation_complete=False,
                interrupted=False,
            ))
            await asyncio.sleep(10)

    class FakeLiveContext:
        def __init__(self, session):
            self.session = session

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, *args):
            return None

    class LiveSocket(FakeSocket):
        def __init__(self):
            super().__init__()
            self.messages = [
                {"text": '{"type":"live_audio_start","sample_rate":16000}'},
                {"text": '{"type":"end"}'},
            ]

        async def receive(self):
            return self.messages.pop(0)

    session = InterruptedLiveSession()
    client = SimpleNamespace(aio=SimpleNamespace(live=SimpleNamespace(
        connect=lambda **kwargs: FakeLiveContext(session)
    )))
    socket = LiveSocket()
    audio_path = str(tmp_path / "interrupted-live.webm")
    with patch.object(gemini_live, "settings", SimpleNamespace(
        gemini_api_key="test-key",
        gemini_live_model="test-live-model",
        gemini_live_voice="Kore",
    )), patch("google.genai.Client", return_value=client):
        transcript, _, ended_reason = asyncio.run(gemini_live.run_gemini_live_pipeline(
            socket, "token", "en", audio_path,
        ))

    assert ended_reason == "completed"
    # The interruption closes the first audio turn. The replacement response
    # has no audio payload in this fixture, so it does not emit another end.
    text_msgs = [json.loads(m) for m in socket.text if m.startswith("{")]
    audio_end_msgs = [m for m in text_msgs if m.get("type") == "audio_end"]
    assert len(audio_end_msgs) == 1
    # The assistant transcript should contain "Of course." (the post-interrupt response)
    assert any("Of course" in t.get("text", "") for t in transcript if t.get("role") == "assistant")


def test_gemini_live_upstream_silence_triggers_watchdog(tmp_path):
    """When Gemini sends no messages, the watchdog should trigger a live_error."""
    from backend.services import gemini_live

    class SilentLiveSession:
        async def send_realtime_input(self, **kwargs):
            pass

        async def send_client_content(self, **kwargs):
            pass

        async def receive(self):
            # Start a model turn, then stop sending messages mid-response.
            yield SimpleNamespace(server_content=SimpleNamespace(
                input_transcription=None,
                output_transcription=None,
                model_turn=SimpleNamespace(parts=[
                    SimpleNamespace(inline_data=SimpleNamespace(data=b"\x00\x01")),
                ]),
                turn_complete=False,
                generation_complete=False,
                interrupted=False,
            ))
            await asyncio.sleep(60)

    class FakeLiveContext:
        def __init__(self, session):
            self.session = session

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, *args):
            return None

    class LiveSocket(FakeSocket):
        def __init__(self):
            super().__init__()
            self.messages = [
                {"text": '{"type":"live_audio_start","sample_rate":16000}'},
            ]
            self._heartbeat_count = 0

        async def receive(self):
            if self.messages:
                return self.messages.pop(0)
            # Simulate heartbeats arriving while Gemini stays silent.
            # Small sleep so the watchdog timeout (0.05s) fires quickly.
            self._heartbeat_count += 1
            if self._heartbeat_count > 100:
                return {"text": '{"type":"end"}'}
            await asyncio.sleep(0.01)
            return {"text": '{"type":"heartbeat"}'}

    session = SilentLiveSession()
    client = SimpleNamespace(aio=SimpleNamespace(live=SimpleNamespace(
        connect=lambda **kwargs: FakeLiveContext(session)
    )))
    socket = LiveSocket()
    audio_path = str(tmp_path / "silent-live.webm")
    # Patch the timeout to 0.05s so the test runs fast
    with patch.object(gemini_live, "settings", SimpleNamespace(
        gemini_api_key="test-key",
        gemini_live_model="test-live-model",
        gemini_live_voice="Kore",
    )), patch("google.genai.Client", return_value=client), \
         patch.object(gemini_live, "GEMINI_SILENCE_TIMEOUT", 0.05):
        transcript, _, ended_reason = asyncio.run(gemini_live.run_gemini_live_pipeline(
            socket, "token", "en", audio_path,
        ))

    assert ended_reason == "gemini_live_error"
    assert any('"type": "live_error"' in message for message in socket.text)


def test_websocket_session_completes_and_processes(client, db):
    token = create_interview(db, token="ws-token", status="active")

    async def fake_pipeline(websocket, token, language, audio_path, *, candidate_name,
                             resume_transcript=None, resume_duration_seconds=0.0):
        assert candidate_name == "Test User"
        assert resume_transcript == []
        assert resume_duration_seconds == 0.0
        await websocket.send_text('{"type":"ready"}')
        message = await websocket.receive()
        assert message["text"] == '{"type":"end"}'
        return [{"role": "user", "text": "Hello", "timestamp": 1}], 10

    with patch("backend.api.routes.session.run_sarah_pipeline", new=fake_pipeline), \
         patch("backend.api.routes.session.process_interview", new=AsyncMock()) as process:
        with client.websocket_connect(f"/ws/interview/{token}") as websocket:
            assert websocket.receive_json() == {"type": "ready"}
            websocket.send_text('{"type":"end"}')

    process.assert_awaited_once()
    row = db.execute("SELECT status FROM interviews WHERE token = ?", (token,)).fetchone()
    assert row["status"] == "active"


def test_websocket_rejects_non_active_interview(client, db):
    token = create_interview(db, token="pending-ws-token", status="pending")
    with client.websocket_connect(f"/ws/interview/{token}") as websocket:
        try:
            websocket.receive()
        except Exception as exc:
            assert "1008" in str(exc) or "WebSocketDisconnect" in type(exc).__name__


def test_websocket_rejects_completed_interview(client, db):
    """A completed interview cannot be resumed -- covers the same 1008 path as the
    pending case above, for the genuinely-finished terminal statuses."""
    token = create_interview(db, token="completed-ws-token", status="completed")
    with client.websocket_connect(f"/ws/interview/{token}") as websocket:
        try:
            websocket.receive()
        except Exception as exc:
            assert "1008" in str(exc) or "WebSocketDisconnect" in type(exc).__name__


# ---------------------------------------------------------------------------
# Reconnect: recoverable disconnect persists the transcript-so-far, and the next
# connection attempt on the same interview picks it back up.
# ---------------------------------------------------------------------------

def test_recoverable_disconnect_persists_transcript_and_does_not_process(client, db):
    token = create_interview(db, token="ws-token", status="active")

    async def fake_pipeline(websocket, token, language, audio_path, *, candidate_name,
                             resume_transcript=None, resume_duration_seconds=0.0):
        return [{"role": "user", "text": "partial answer", "timestamp": 3}], 45.0, "browser_disconnect"

    with patch("backend.api.routes.session.run_sarah_pipeline", new=fake_pipeline), \
         patch("backend.api.routes.session.process_interview", new=AsyncMock()) as process:
        with client.websocket_connect(f"/ws/interview/{token}"):
            pass

    # A recoverable failure must never reach process_interview -- no evaluation, no
    # report, no webhook for a session that never actually concluded.
    process.assert_not_awaited()
    row = db.execute("SELECT * FROM interviews WHERE token = ?", (token,)).fetchone()
    assert row["status"] == "pending"
    assert row["retry_count"] == 1
    assert json.loads(row["pending_transcript"]) == [
        {"role": "user", "text": "partial answer", "timestamp": 3}
    ]
    assert row["pending_duration_seconds"] == 45.0


def test_reconnect_forwards_pending_transcript_and_merges_result(client, db):
    """The next connection attempt on the same (still-pending, now reactivated)
    interview must load the prior leg's transcript/duration and pass them through --
    and the merged result (built by the pipeline itself; see gemini_live.py) is what
    reaches process_interview, not just the new leg's own turns."""
    token = create_interview(db, token="ws-token", status="active", retry_count=1)
    db.execute(
        "UPDATE interviews SET pending_transcript = ?, pending_duration_seconds = ? WHERE token = ?",
        (json.dumps([{"role": "user", "text": "partial answer", "timestamp": 3}]), 45.0, token),
    )
    db.commit()

    captured = {}

    async def fake_pipeline(websocket, token, language, audio_path, *, candidate_name,
                             resume_transcript=None, resume_duration_seconds=0.0):
        captured["resume_transcript"] = resume_transcript
        captured["resume_duration_seconds"] = resume_duration_seconds
        await websocket.send_text('{"type":"ready"}')
        await websocket.receive()
        merged = list(resume_transcript) + [{"role": "user", "text": "second leg", "timestamp": 1}]
        return merged, resume_duration_seconds + 30.0, "completed"

    with patch("backend.api.routes.session.run_sarah_pipeline", new=fake_pipeline), \
         patch("backend.api.routes.session.process_interview", new=AsyncMock()) as process:
        with client.websocket_connect(f"/ws/interview/{token}") as websocket:
            assert websocket.receive_json() == {"type": "ready"}
            websocket.send_text('{"type":"end"}')

    assert captured["resume_transcript"] == [{"role": "user", "text": "partial answer", "timestamp": 3}]
    assert captured["resume_duration_seconds"] == 45.0
    process.assert_awaited_once()
    call_args = process.call_args.args
    merged_transcript, merged_duration = call_args[1], call_args[3]
    assert merged_transcript == [
        {"role": "user", "text": "partial answer", "timestamp": 3},
        {"role": "user", "text": "second leg", "timestamp": 1},
    ]
    assert merged_duration == 75.0


def test_disconnect_beyond_retry_budget_leaves_interview_active(client, db):
    """Once the shared reconnect budget is exhausted, a further disconnect must not
    reset status again -- the interview simply stays 'active' and unreachable rather
    than looping forever, and never gets a report (see deps.py for the /start-side
    half of this same bound)."""
    from backend.database import MAX_INTERVIEW_RECONNECTS

    token = create_interview(db, token="ws-token", status="active", retry_count=MAX_INTERVIEW_RECONNECTS)

    async def fake_pipeline(websocket, token, language, audio_path, *, candidate_name,
                             resume_transcript=None, resume_duration_seconds=0.0):
        return [{"role": "user", "text": "one more try", "timestamp": 1}], 10.0, "browser_disconnect"

    with patch("backend.api.routes.session.run_sarah_pipeline", new=fake_pipeline), \
         patch("backend.api.routes.session.process_interview", new=AsyncMock()) as process:
        with client.websocket_connect(f"/ws/interview/{token}"):
            pass

    process.assert_not_awaited()
    row = db.execute("SELECT * FROM interviews WHERE token = ?", (token,)).fetchone()
    assert row["status"] == "active"
    assert row["retry_count"] == MAX_INTERVIEW_RECONNECTS
    assert row["pending_transcript"] is None


# ---------------------------------------------------------------------------
# Single-owner guard: two simultaneous sockets cannot independently own one
# interview.
# ---------------------------------------------------------------------------

def test_second_concurrent_websocket_for_same_interview_is_rejected(client, db):
    token = create_interview(db, token="concurrent-token", status="active")
    entered = threading.Event()
    release = threading.Event()

    async def slow_pipeline(websocket, token, language, audio_path, *, candidate_name,
                             resume_transcript=None, resume_duration_seconds=0.0):
        entered.set()
        await websocket.send_text('{"type":"ready"}')
        while not release.is_set():
            await asyncio.sleep(0.01)
        await websocket.receive()
        return [{"role": "user", "text": "hi", "timestamp": 1}], 5.0, "completed"

    # session.py runs process_interview *after* `await websocket.close()`, so the
    # side-effect this test asserts on happens once the client has already gone away.
    # Asserting on it straight after the `with` block is therefore a race: it passed on
    # Windows and failed in CI on Linux with "Awaited 0 times". Signalling an event from
    # the mock and waiting for it turns the race into a deterministic wait, without
    # weakening what is being checked.
    processed = threading.Event()

    async def fake_process(*args, **kwargs):
        processed.set()

    process = AsyncMock(side_effect=fake_process)

    with patch("backend.api.routes.session.run_sarah_pipeline", new=slow_pipeline), \
         patch("backend.api.routes.session.process_interview", new=process):
        with client.websocket_connect(f"/ws/interview/{token}") as first:
            assert first.receive_json() == {"type": "ready"}
            assert entered.wait(timeout=2), "first connection's pipeline never started"

            # A second connection attempt for the exact same token, while the first
            # is still bridging it, must be rejected immediately -- never allowed to
            # start its own concurrent pipeline.
            with client.websocket_connect(f"/ws/interview/{token}") as second:
                message = second.receive_json()
                assert message["type"] == "already_active"

            release.set()
            first.send_text('{"type":"end"}')

        assert processed.wait(timeout=5), "process_interview was never awaited"

    process.assert_awaited_once()
    row = db.execute("SELECT * FROM interviews WHERE token = ?", (token,)).fetchone()
    # The rejected second connection never touched interview state.
    assert row["retry_count"] == 0

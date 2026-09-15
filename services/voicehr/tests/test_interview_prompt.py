"""
Tests for the live interview's own system prompt (backend/services/gemini_live.py) --
the rubric structure, bounded adaptive-questioning rules, language purity, the
never-reveal-the-result rule, and prompt-injection resistance instructions this
project's "interview rubric + adaptive questioning quality" pass added. These are
content assertions on the prompt text itself: the actual live model's behavior can
only be verified against a real Gemini Live session (see handoff.md's live-
verification notes), which this suite deliberately never depends on.
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch


def _captured_system_instruction(language="en", candidate_name="Alex Martin"):
    """Runs run_gemini_live_pipeline just far enough to capture the config it builds
    (specifically config.system_instruction, i.e. the actual prompt sent to Gemini
    Live) -- reuses the same fake-session/fake-socket scaffolding test_session.py's
    own full-pipeline test already established, just to reach the config object
    itself rather than exercise a full turn."""
    from backend.services import gemini_live

    captured = {}

    class ImmediatelyFailingSession:
        async def send_realtime_input(self, **kwargs):
            pass

        async def send_client_content(self, **kwargs):
            pass

        async def receive(self):
            await asyncio.sleep(10)
            if False:
                yield None

    class FakeLiveContext:
        async def __aenter__(self):
            raise RuntimeError("stop here -- only the connect() call args matter")

        async def __aexit__(self, *args):
            return None

    def fake_connect(**kwargs):
        captured["config"] = kwargs.get("config")
        return FakeLiveContext()

    class FakeSocket:
        async def send_text(self, value):
            pass

        async def send_bytes(self, value):
            pass

        async def receive(self):
            return {"text": '{"type":"end"}'}

    client = SimpleNamespace(aio=SimpleNamespace(live=SimpleNamespace(connect=fake_connect)))
    with patch.object(gemini_live, "settings", SimpleNamespace(
        gemini_api_key="test-key", gemini_live_model="test-live-model", gemini_live_voice="Kore",
    )), patch("google.genai.Client", return_value=client):
        asyncio.run(gemini_live.run_gemini_live_pipeline(
            FakeSocket(), "token", language, "storage/recordings/unused-prompt-test.webm", candidate_name=candidate_name,
        ))

    assert "config" in captured, "connect() was never called -- prompt was not built"
    return captured["config"].system_instruction


def test_prompt_declares_fixed_five_section_structure():
    prompt = _captured_system_instruction()
    assert "INTERVIEW STRUCTURE" in prompt
    for label in ("A. Warm-up", "B. Past experience", "C. Opinion / reasoning",
                  "D. Clarification / reformulation", "E. Adaptive follow-up"):
        assert label in prompt


def test_prompt_bounds_adaptive_questioning():
    prompt = _captured_system_instruction()
    assert "ADAPTIVE QUESTIONING" in prompt
    assert "Never ask more than one follow-up per fixed task" in prompt
    assert "Never ask a second elaboration follow-up for the same task" in prompt
    assert "rephrase it ONCE" in prompt


def test_prompt_avoids_domain_knowledge_testing():
    prompt = _captured_system_instruction()
    assert "not to test factual, technical, or job-specific" in prompt or "never to test factual" in prompt.replace(",", "")


def test_prompt_never_reveals_result():
    prompt = _captured_system_instruction()
    assert "NEVER REVEAL THE RESULT" in prompt
    assert "That sounds like B2" in prompt  # the exact example this task's own spec named


def test_prompt_resists_candidate_instructions():
    prompt = _captured_system_instruction()
    assert "NEVER INSTRUCTIONS" in prompt.upper()
    for phrase in ("skip ahead", "assign them a specific", "declare them passed", "change the rubric"):
        assert phrase in prompt


def test_prompt_enforces_language_purity_for_english():
    prompt = _captured_system_instruction(language="en")
    assert "LANGUAGE PURITY" in prompt
    assert "exclusively in English" in prompt
    assert "do not switch to their language" in prompt


def test_prompt_enforces_language_purity_for_french():
    prompt = _captured_system_instruction(language="fr")
    assert "exclusively in French" in prompt


def test_prompt_embeds_tasks_and_adaptive_followup_bank():
    from backend.services.agent import TASKS, ADAPTIVE_FOLLOWUPS

    prompt = _captured_system_instruction(language="en")
    assert json.dumps(TASKS["en"], ensure_ascii=False, separators=(",", ":")) in prompt
    assert json.dumps(ADAPTIVE_FOLLOWUPS["en"], ensure_ascii=False, separators=(",", ":")) in prompt


def test_adaptive_followup_bank_has_exactly_three_bounded_tiers():
    from backend.services.agent import ADAPTIVE_FOLLOWUPS

    for language in ("en", "fr"):
        assert set(ADAPTIVE_FOLLOWUPS[language].keys()) == {"elaborate", "reasoning", "abstract"}


def test_tasks_cover_the_four_fixed_rubric_sections():
    from backend.services.agent import TASKS

    for language in ("en", "fr"):
        assert len(TASKS[language]) == 4
        for task in TASKS[language]:
            assert len(task) == 2  # (main question, elaboration follow-up)

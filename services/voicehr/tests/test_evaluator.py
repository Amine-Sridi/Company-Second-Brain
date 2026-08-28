import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock


class TestEvaluator(unittest.TestCase):

    @patch("backend.services.evaluator.gemini_client")
    def test_evaluate_returns_expected_keys(self, mock_gemini):
        mock_gemini.aio.models.generate_content.return_value = MagicMock(text=json.dumps({
            "interaction_comprehension_score": 80,
            "fluency_coherence_score": 70,
            "grammar_language_control_score": 68,
            "vocabulary_range_score": 75,
            "pronunciation_intelligibility_score": 74,
            "strengths": [{"text": "Good fluency", "timestamp": "02:14"}],
            "concerns": [],
            "evidence": [{"timestamp": "02:14", "quote": "test", "dimension": "Fluency", "note": "Good"}],
            "candidate_speaking_time": 420,
            "word_count": 1200,
        }))

        from backend.services.evaluator import evaluate
        import asyncio

        transcript = [
            {"role": "assistant", "text": "Hello", "timestamp": 0},
            {"role": "user", "text": "Hi there", "timestamp": 5},
        ]
        with patch("backend.services.evaluator.settings", SimpleNamespace(
            gemini_model="gemini-2.5-flash", gemini_evaluation_model="gemini-2.5-flash",
            gemini_api_key="test-key",
        )):
            result = asyncio.run(evaluate(transcript))

        self.assertIn("overall_score", result)
        self.assertIn("cefr_level", result)
        self.assertIn("strengths", result)
        # The application applies the requested weights, not the model's
        # legacy overall_score (80*.25 + 70*.25 + 68*.20 + 75*.15 + 74*.15).
        self.assertEqual(result["overall_score"], 73)
        self.assertEqual(result["cefr_level"], "B2")

    @patch("backend.services.evaluator.gemini_client")
    def test_evaluate_handles_api_error(self, mock_gemini):
        """A total provider failure (not just an incomplete response) must still fall
        back to empty_result gracefully rather than raising past evaluate()."""
        mock_gemini.aio.models.generate_content.side_effect = Exception("API Error")

        from backend.services.evaluator import evaluate
        import asyncio

        transcript = [{"role": "user", "text": "Hello", "timestamp": 0}]
        with patch("backend.services.evaluator.settings", SimpleNamespace(
            gemini_model="gemini-2.5-flash", gemini_evaluation_model="gemini-2.5-flash",
            gemini_api_key="test-key",
        )):
            result = asyncio.run(evaluate(transcript))

        self.assertIsNone(result["overall_score"])
        self.assertEqual(result["evaluation_status"], "failed")
        self.assertIn("error", result)

    @patch("backend.services.evaluator.gemini_client")
    def test_evaluate_inconclusive_flag(self, mock_gemini):
        mock_gemini.aio.models.generate_content.return_value = MagicMock(text=json.dumps({
            "interaction_comprehension_score": 40,
            "fluency_coherence_score": 25,
            "grammar_language_control_score": 28,
            "vocabulary_range_score": 30,
            "pronunciation_intelligibility_score": 35,
            "strengths": [],
            "concerns": [{"text": "Too short", "timestamp": "00:00"}],
            "evidence": [],
            "candidate_speaking_time": 120,
            "word_count": 200,
        }))

        from backend.services.evaluator import evaluate
        import asyncio

        transcript = [{"role": "user", "text": "Hi", "timestamp": 0}]
        with patch("backend.services.evaluator.settings", SimpleNamespace(
            gemini_model="gemini-2.5-flash", gemini_evaluation_model="gemini-2.5-flash",
            gemini_api_key="test-key",
        )):
            result = asyncio.run(evaluate(transcript, inconclusive=True))

        self.assertEqual(result["overall_score"], 32)
        self.assertEqual(result["confidence"], "Low")
        prompt_content = mock_gemini.aio.models.generate_content.call_args.kwargs["contents"]
        self.assertIn("incomplete", prompt_content.lower())

    def test_normalize_result_applies_requested_weights_and_metrics(self):
        from backend.services.evaluator import normalize_result

        transcript = [
            {"role": "user", "text": "I lead teams.", "timestamp": 5},
            {"role": "user", "text": "I solved the issue.", "timestamp": 20},
            {"role": "user", "text": "Remote work is effective.", "timestamp": 40},
        ]
        result = normalize_result({
            "overall_score": 1,
            "dimensions": {
                "interaction_comprehension": {"score": 80},
                "fluency_coherence": {"score": 60},
                "grammar_language_control": {"score": 70},
                "vocabulary_range": {"score": 50},
                "pronunciation_intelligibility": {"score": 90},
            },
            "evidence": [{"timestamp": "00:05", "quote": "I lead teams.", "dimension": "Fluency", "note": "Clear"}],
        }, transcript, duration_seconds=600)

        self.assertEqual(result["overall_score"], 70)
        self.assertEqual(result["cefr_level"], "B2")
        self.assertEqual(result["confidence"], "High")
        self.assertEqual(result["word_count"], 11)
        self.assertGreater(result["candidate_speaking_time"], 0)

    @patch("backend.services.evaluator.gemini_client")
    def test_evaluate_uses_gemini_structured_response(self, mock_gemini):
        from backend.services.evaluator import evaluate
        import asyncio

        response = MagicMock()
        response.text = json.dumps({
            "interaction_comprehension_score": 80,
            "fluency_coherence_score": 70,
            "grammar_language_control_score": 75,
            "vocabulary_range_score": 65,
            "pronunciation_intelligibility_score": 70,
            "evidence": [{"timestamp": "00:05", "quote": "I lead teams", "dimension": "Fluency", "note": "Clear"}],
        })
        mock_gemini.aio.models.generate_content.return_value = response
        transcript = [
            {"role": "user", "text": "I lead teams.", "timestamp": 5},
            {"role": "user", "text": "I solved the issue.", "timestamp": 20},
            {"role": "user", "text": "Remote work is effective.", "timestamp": 40},
        ]

        with patch("backend.services.evaluator.settings", SimpleNamespace(
            gemini_model="gemini-2.5-flash",
            gemini_evaluation_model="gemini-2.5-flash", gemini_api_key="test-key"
        )):
            result = asyncio.run(evaluate(transcript, duration_seconds=600))

        assert result["evaluation_status"] == "completed"
        assert result["overall_score"] == 73
        assert result["dimensions"]["interaction_comprehension"] == 80
        mock_gemini.aio.models.generate_content.assert_called_once()
        assert mock_gemini.aio.models.generate_content.call_args.kwargs["model"] == "gemini-2.5-flash"

    @patch("backend.services.evaluator.gemini_client")
    def test_evaluate_retries_once_after_incomplete_gemini_response(self, mock_gemini):
        """Syntactically valid JSON missing a dimension score fails our completeness
        check -- that's worth exactly one fresh generation attempt before giving up."""
        from backend.services.evaluator import evaluate
        import asyncio

        incomplete = MagicMock()
        incomplete.text = json.dumps({
            "interaction_comprehension_score": 80,
            "fluency_coherence_score": 70,
            "grammar_language_control_score": 75,
            "vocabulary_range_score": 65,
            # pronunciation_intelligibility_score missing -> fails completeness check.
            "evidence": [],
        })
        complete = MagicMock()
        complete.text = json.dumps({
            "interaction_comprehension_score": 80,
            "fluency_coherence_score": 70,
            "grammar_language_control_score": 75,
            "vocabulary_range_score": 65,
            "pronunciation_intelligibility_score": 70,
            "evidence": [],
        })
        mock_gemini.aio.models.generate_content.side_effect = [incomplete, complete]

        transcript = [{"role": "user", "text": "I lead teams.", "timestamp": 5}]
        with patch("backend.services.evaluator.settings", SimpleNamespace(
            gemini_model="gemini-2.5-flash",
            gemini_evaluation_model="gemini-2.5-flash", gemini_api_key="test-key",
        )):
            result = asyncio.run(evaluate(transcript, duration_seconds=600))

        assert result["evaluation_status"] == "completed"
        assert result["overall_score"] is not None
        assert mock_gemini.aio.models.generate_content.call_count == 2

    @patch("backend.services.evaluator.gemini_client")
    def test_evaluate_gives_up_after_bounded_retries_on_persistent_incomplete_response(self, mock_gemini):
        """A persistently incomplete response must still fail cleanly (graceful
        fallback to empty_result) after exactly one retry -- never an unbounded loop."""
        from backend.services.evaluator import evaluate
        import asyncio

        incomplete = MagicMock()
        incomplete.text = json.dumps({
            "interaction_comprehension_score": 80,
            "fluency_coherence_score": 70,
            "grammar_language_control_score": 75,
            "vocabulary_range_score": 65,
            "evidence": [],
        })
        # Three available responses, but only two must ever be consumed.
        mock_gemini.aio.models.generate_content.side_effect = [incomplete, incomplete, incomplete]

        transcript = [{"role": "user", "text": "I lead teams.", "timestamp": 5}]
        with patch("backend.services.evaluator.settings", SimpleNamespace(
            gemini_model="gemini-2.5-flash",
            gemini_evaluation_model="gemini-2.5-flash", gemini_api_key="test-key",
        )):
            result = asyncio.run(evaluate(transcript, duration_seconds=600))

        assert result["overall_score"] is None
        assert result["evaluation_status"] == "failed"
        assert "error" in result
        assert mock_gemini.aio.models.generate_content.call_count == 2

    def test_normalize_result_resolves_legacy_flat_aliases(self):
        """DIMENSION_ALIASES must resolve short/legacy flat field names (as opposed to
        the nested {"dimensions": {"name": {"score": N}}} shape already covered above)
        to the same normalized result."""
        from backend.services.evaluator import normalize_result

        transcript = [{"role": "user", "text": "I lead teams.", "timestamp": 5}]
        result = normalize_result({
            "comprehension_score": 80,
            "fluency_score": 60,
            "grammar_score": 70,
            "vocabulary_score": 50,
            "pronunciation_score": 90,
        }, transcript, duration_seconds=600)

        self.assertEqual(result["comprehension_score"], 80)
        self.assertEqual(result["fluency_score"], 60)
        self.assertEqual(result["grammar_score"], 70)
        self.assertEqual(result["vocabulary_score"], 50)
        self.assertEqual(result["pronunciation_score"], 90)
        self.assertEqual(result["overall_score"], 70)

    def test_evaluation_prompt_treats_transcript_as_untrusted_data(self):
        """Regression test for prompt hardening: the transcript must be delimited and
        the model explicitly told not to follow instructions embedded in it -- a
        candidate has a direct incentive to try to manipulate their own score."""
        from backend.services.evaluator import EVALUATION_PROMPT

        self.assertIn("<transcript>", EVALUATION_PROMPT)
        self.assertIn("</transcript>", EVALUATION_PROMPT)
        self.assertIn("not instructions", EVALUATION_PROMPT.lower())


if __name__ == "__main__":
    unittest.main()

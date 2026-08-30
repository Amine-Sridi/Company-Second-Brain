"""
tests/test_second_brain_client.py
------------------------------------
Mocked unit tests for second_brain_client.py -- no network access. Mocks
shared.http_client.call_service directly (the same layer RWS's own
cross-service calls go through), since correlation-ID propagation and
transport-error handling are call_service's own responsibility and already
exercised there -- not duplicated here.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx

from second_brain_client import SecondBrainClientError, send_candidate_summary
from shared.http_client import ServiceCallError
from shared.schemas import CandidateSummary


def _run(coro):
    return asyncio.run(coro)


def _summary() -> CandidateSummary:
    return CandidateSummary(
        candidate_id=uuid.uuid4(),
        skills=["Python"],
        years_of_experience=3.5,
        job_titles=["Backend Engineer"],
        raw_text="Backend Engineer at Acme. Skills: Python.",
        source_version="1",
    )


class TestSendCandidateSummary:
    def test_success_calls_the_expected_endpoint_with_the_payload(self):
        summary = _summary()
        fake_response = httpx.Response(200, json={"candidate_id": str(summary.candidate_id), "status": "ok"})

        with patch("second_brain_client.call_service", new=AsyncMock(return_value=fake_response)) as mock_call:
            _run(send_candidate_summary("http://second-brain:8000", "test-key", summary))

        mock_call.assert_awaited_once()
        args, kwargs = mock_call.await_args
        assert args[0] == "POST"
        assert args[1] == "http://second-brain:8000/v1/knowledge/candidates"
        assert kwargs["api_key"] == "test-key"
        assert kwargs["json"]["candidate_id"] == str(summary.candidate_id)
        assert kwargs["json"]["raw_text"] == summary.raw_text

    def test_non_200_response_raises_second_brain_client_error(self):
        summary = _summary()
        fake_response = httpx.Response(422, json={"detail": "validation error"})

        with patch("second_brain_client.call_service", new=AsyncMock(return_value=fake_response)):
            try:
                _run(send_candidate_summary("http://second-brain:8000", "test-key", summary))
                assert False, "expected SecondBrainClientError"
            except SecondBrainClientError:
                pass

    def test_transport_failure_is_wrapped_as_second_brain_client_error(self):
        summary = _summary()

        async def _raise(*args, **kwargs):
            raise ServiceCallError("connection refused")

        with patch("second_brain_client.call_service", new=_raise):
            try:
                _run(send_candidate_summary("http://second-brain:8000", "test-key", summary))
                assert False, "expected SecondBrainClientError"
            except SecondBrainClientError:
                pass

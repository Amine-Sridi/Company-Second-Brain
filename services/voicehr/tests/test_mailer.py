import asyncio
from unittest.mock import patch, AsyncMock

import pytest

from backend.services.mailer import send_report_email


@pytest.mark.asyncio
async def test_send_report_email_success():
    mock_response = AsyncMock()
    mock_response.status_code = 200

    with patch("backend.services.mailer.httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_class.return_value.__aenter__.return_value = mock_client

        result = await send_report_email(
            candidate_name="John Doe",
            admin_email="admin@test.com",
            report_link="http://test.com/report",
            scores={"overall_score": 72, "cefr_level": "B2"},
        )

        assert result is True


@pytest.mark.asyncio
async def test_send_report_email_failure():
    mock_response = AsyncMock()
    mock_response.status_code = 500

    with patch("backend.services.mailer.httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_class.return_value.__aenter__.return_value = mock_client

        result = await send_report_email(
            candidate_name="John Doe",
            admin_email="admin@test.com",
            report_link="http://test.com/report",
            scores={"overall_score": 72, "cefr_level": "B2"},
        )

        assert result is False


@pytest.mark.asyncio
async def test_send_report_email_from_address_never_contains_api_key():
    """
    Regression test: the 'from' field must never be built from resend_api_key. It
    previously read f"onboarding@{settings.resend_api_key}" -- a live secret placed
    into an outbound email header rather than a real sender address.
    """
    mock_response = AsyncMock()
    mock_response.status_code = 200

    with patch("backend.services.mailer.settings.resend_api_key", "super-secret-resend-key"), \
         patch("backend.services.mailer.settings.resend_from_address", "VoiceHR <test@example.com>"), \
         patch("backend.services.mailer.httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_class.return_value.__aenter__.return_value = mock_client

        await send_report_email(
            candidate_name="John Doe",
            admin_email="admin@test.com",
            report_link="http://test.com/report",
            scores={"overall_score": 72, "cefr_level": "B2"},
        )

        call_kwargs = mock_client.post.call_args.kwargs
        assert call_kwargs["json"]["from"] == "VoiceHR <test@example.com>"
        assert "super-secret-resend-key" not in call_kwargs["json"]["from"]


@pytest.mark.asyncio
async def test_send_report_email_falls_back_to_sandbox_sender_when_unset():
    """An unset/empty resend_from_address must fall back to a safe default, never to
    an empty or key-derived 'from' value."""
    mock_response = AsyncMock()
    mock_response.status_code = 200

    with patch("backend.services.mailer.settings.resend_api_key", "super-secret-resend-key"), \
         patch("backend.services.mailer.settings.resend_from_address", ""), \
         patch("backend.services.mailer.httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_class.return_value.__aenter__.return_value = mock_client

        await send_report_email(
            candidate_name="John Doe",
            admin_email="admin@test.com",
            report_link="http://test.com/report",
            scores={"overall_score": 72, "cefr_level": "B2"},
        )

        call_kwargs = mock_client.post.call_args.kwargs
        assert call_kwargs["json"]["from"]
        assert "super-secret-resend-key" not in call_kwargs["json"]["from"]


@pytest.mark.asyncio
async def test_send_report_email_times_out():
    async def slow_post(*args, **kwargs):
        await asyncio.sleep(0.05)

    with patch("backend.services.mailer.settings.report_email_timeout_seconds", 0.001), \
         patch("backend.services.mailer.httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.post = slow_post
        mock_client_class.return_value.__aenter__.return_value = mock_client

        result = await send_report_email(
            candidate_name="John Doe",
            admin_email="admin@test.com",
            report_link="http://test.com/report",
            scores={"overall_score": 72, "cefr_level": "B2"},
        )

    assert result is False

import asyncio

import httpx

from backend.core.config import settings


async def send_report_email(candidate_name: str, admin_email: str, report_link: str, scores: dict):
    timeout_seconds = getattr(settings, "report_email_timeout_seconds", 180.0)
    from_address = getattr(settings, "resend_from_address", None) or "VoiceHR <onboarding@resend.dev>"
    payload = {
        "from": from_address,
        "to": [admin_email],
        "subject": f"VoiceHR Report: {candidate_name}",
        "html": f"""
        <h2>Interview Report Ready</h2>
        <p><strong>Candidate:</strong> {candidate_name}</p>
        <p><strong>Overall Score:</strong> {scores.get('overall_score', 'N/A')}</p>
        <p><strong>CEFR Level:</strong> {scores.get('cefr_level', 'N/A')}</p>
        <p><a href="{report_link}">View Full Report</a></p>
        """,
    }

    async def deliver():
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {settings.resend_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            return resp.status_code == 200

    try:
        return await asyncio.wait_for(deliver(), timeout=timeout_seconds)
    except (asyncio.TimeoutError, httpx.HTTPError):
        return False

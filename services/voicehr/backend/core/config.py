from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Gemini is the sole AI provider for this project (see CLAUDE.md) -- both for
    # interview evaluation and for the Gemini Live interview-conduct pipeline.
    gemini_api_key: Optional[str] = None
    gemini_model: str = "gemini-2.5-flash"
    gemini_evaluation_model: str = "gemini-3.1-flash-lite-preview"
    gemini_live_model: str = "gemini-3.1-flash-live-preview"
    gemini_live_voice: str = "Kore"
    resend_api_key: Optional[str] = None
    # Resend's own unverified-domain sandbox sender -- works without a verified custom
    # domain. Must never be the API key itself (that was a bug: see mailer.py history).
    resend_from_address: str = "VoiceHR <onboarding@resend.dev>"
    admin_email: Optional[str] = None
    # HTTP Basic auth for the admin/report-management surface (candidate PII,
    # transcripts, recordings, scores, retry actions) -- one shared credential pair,
    # not a user database. Change this in any real deployment.
    admin_username: str = "admin"
    admin_password: str = "changeme-admin-password"
    report_email_timeout_seconds: float = 180.0
    # The candidate invitation email is sent synchronously inside POST /api/invite's
    # own request/response cycle (see api/routes/invite.py), which RWS calls with a
    # 10s client timeout (shared/http_client.py: DEFAULT_TIMEOUT_SECONDS) -- this must
    # stay comfortably under that so a slow Resend call fails cleanly instead of RWS
    # timing out first and reporting an ambiguous transport error.
    invite_email_timeout_seconds: float = 8.0
    base_url: str = "http://localhost:8000"

    # RWS integration (this project only, layered on top of the existing app).
    # rws_service_api_key: checked against X-Api-Key on inbound POST /api/invite calls.
    # rws_webhook_hmac_secret: signs the outbound interview-result webhook to RWS.
    # rws_webhook_url: where that webhook is POSTed.
    rws_service_api_key: str = "changeme-voicehr-key"
    rws_webhook_hmac_secret: str = "changeme-webhook-secret"
    rws_webhook_url: Optional[str] = None

settings = Settings()

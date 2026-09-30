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
    # GET /api/report/{report_token} is an unauthenticated bearer URL carrying the
    # candidate's name, email, scores and full transcript, and the link is emailed to
    # ADMIN_EMAIL on completion. It used to live forever, so a mailbox breach years
    # later still yielded a live PII endpoint. Matches the interview link's own 7-day
    # window (api/routes/invite.py). The authenticated admin and RWS surfaces are
    # deliberately NOT subject to it -- see api/routes/report.py. Audit M10.
    report_token_ttl_days: int = 7
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

    # Local evaluation fallback (benchmarks/local_llm, milestone 61). Off unless
    # explicitly enabled, so the default deployment keeps Gemini as the sole
    # evaluation provider exactly as before.
    #
    # It is deliberately NOT a drop-in replacement for Gemini: the benchmark showed
    # gemma4:e2b-it-qat compresses the CEFR scale (never issues A1 or C2, and rates
    # the weak A1/A2 golden sample as B1), so its verdicts are recorded as advisory
    # only and never become an authoritative CEFR -- see _fallback_advisory_result
    # in services/evaluator.py. The model name is configurable, but changing it
    # without re-running benchmarks/local_llm/evaluation_deep_dive.py means shipping
    # an unmeasured model into candidate assessments.
    # Names match RWS and Resume Intelligence (local_llm_enabled/local_llm_base_url)
    # so one LOCAL_LLM_BASE_URL in the environment configures every service. The
    # default is host.docker.internal, not localhost, for the same reason it is there:
    # Ollama runs on the *host*, and inside a container localhost is the container.
    local_llm_enabled: bool = False
    local_llm_base_url: str = "http://host.docker.internal:11434"
    # Deliberately its own setting rather than reusing local_llm_structured_model:
    # the model that won JD structuring (granite4.1:3b) is near the bottom on
    # evaluation, so sharing one name would silently swap in a worse evaluator.
    local_llm_evaluation_model: str = "gemma4:e2b-it-qat"
    # Generous because this runs after the WebSocket has already closed
    # (processing.process_interview), so no user is waiting on it. Measured median
    # was 5.9s on the benchmark machine; a cold model load is the slow case.
    local_llm_evaluation_timeout_seconds: float = 300.0

settings = Settings()

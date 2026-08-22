# VoiceHR (integration surface only)

VoiceHR is a pre-existing, working system. This directory does **not** contain a copy of
VoiceHR's engine or database. Do not migrate its SQLite persistence. Do not rewrite its
interview engine. Do not redesign its internal architecture.

This directory holds only the minimum integration additions:
- `external_reference_id` column (stores/echoes RWS's `application_id`)
- `POST /v1/interviews` invitation endpoint (service-key auth, attempt-scoped idempotency key)
- Outbound signed webhook to RWS on interview completion (`X-Signature` HMAC-SHA256,
  `X-Delivery-Id` for dedup), payload: `{application_id, interview_id, status, assessment}` —
  no audio, no transcript

**Status:** skeleton only. Not yet implemented. **Requires the actual existing VoiceHR
source code before any of the above can be added** — not yet available in this environment
(see `handoff.md` → Known Limitations).

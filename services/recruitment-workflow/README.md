# Recruitment Workflow Service (RWS)

Owns Job, JobDescription (versioned), ScoringConfig (versioned), the matching engine,
workflow state, Application, MatchResult (immutable), RecruiterDecision, and recruiter
authentication (JWT, one seeded account).

**Status:** skeleton only. Not yet implemented.

Planned responsibilities (see the full handoff for the matching formula and state machine):
- Deterministic + semantic hybrid scoring, hard gate, versioned threshold
- LLM explanation generated only after `final_score`/`qualified` are persisted (advisory-only)
- Calls: Second Brain (retrieval + feedback write-back), Resume Intelligence (contact lookup,
  transient), VoiceHR (invitation, idempotent)
- Receives: VoiceHR webhook (HMAC-signed, delivery-id deduped)

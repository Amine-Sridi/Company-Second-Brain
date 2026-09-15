"""
Shared interview-content constants and safety classification for the Gemini Live
interview pipeline (backend/services/gemini_live.py) -- the only interview-conduct
implementation this project uses. The legacy local Whisper/Ollama/Kokoro/ElevenLabs
pipeline that used to live in this module has been removed; Gemini Live conducts the
conversation natively (audio in, audio out, its own turn-taking) and needs none of it.

run_sarah_pipeline is kept as a one-line entry point so backend/api/routes/session.py's
import doesn't need to change.
"""
import re


TASKS = {
    "en": [
        (
            "To begin, could you tell me a little about yourself?",
            "Could you tell me a bit more about your day-to-day responsibilities?",
        ),
        (
            "Can you describe a challenge you faced at work or in your studies, what actions you took, and what the result was?",
            "What specific steps did you personally take to address it?",
        ),
        (
            "Some people believe remote work is more productive than working in an office. What is your view on this, and why?",
            "Can you think of any arguments on the other side?",
        ),
        (
            "I will read you a short message. Please summarize it in your own words. The company has decided to postpone the product launch due to supply chain delays. Teams should focus on internal testing and documentation until further notice. How would you explain that to a colleague?",
            "Could you summarize the main decision and what the teams should do next?",
        ),
    ],
    "fr": [
        (
            "Pour commencer, pouvez-vous me parler un peu de vous ?",
            "Pouvez-vous me décrire un peu plus vos responsabilités au quotidien ?",
        ),
        (
            "Pouvez-vous me décrire un défi rencontré au travail ou dans vos études, les actions prises et le résultat obtenu ?",
            "Quelles mesures avez-vous personnellement prises pour y faire face ?",
        ),
        (
            "Certaines personnes pensent que le télétravail est plus productif que le travail en bureau. Quel est votre point de vue et pourquoi ?",
            "Pouvez-vous penser à des arguments en faveur de l'autre point de vue ?",
        ),
        (
            "Je vais vous lire un court message. Merci de le résumer avec vos propres mots. L'entreprise a décidé de reporter le lancement du produit en raison de retards dans la chaîne d'approvisionnement. Les équipes doivent se concentrer sur les tests internes et la documentation jusqu'à nouvel ordre. Comment l'expliqueriez-vous à un collègue ?",
            "Pouvez-vous résumer la décision principale et ce que les équipes doivent faire ensuite ?",
        ),
    ],
}
# TASKS above map onto the interview rubric's four fixed sections, in order:
#   A. Warm-up             -- task 1 (background)
#   B. Past experience     -- task 2 (a real challenge, actions taken, result)
#   C. Opinion / reasoning -- task 3 (a stance and why, on a domain-neutral topic --
#                             never a technical/trivia question; the goal is language,
#                             not subject-matter expertise)
#   D. Clarification / reformulation -- task 4 (paraphrase a short message in the
#                             candidate's own words)
# Each tuple's second string is that task's own single elaboration probe, used at
# most once, only if that task's initial answer was very short/minimal.

# Section E: one additional adaptive follow-up, asked once after the four fixed
# tasks above, chosen by the interviewer from this small, fixed bank based on
# observed candidate ability -- the one place genuine "adaptive questioning"
# happens. Deliberately topic-agnostic (usable after any of the four tasks) and
# deliberately small (three fixed tiers): bounded adaptation, not an invented,
# unbounded follow-up question.
ADAPTIVE_FOLLOWUPS = {
    "en": {
        "elaborate": "Could you tell me a bit more about that, maybe with a specific example?",
        "reasoning": "That's helpful -- what made you feel that way, or what was your reasoning?",
        "abstract": "Interesting. How do you think that might look different in another situation, or from someone else's point of view?",
    },
    "fr": {
        "elaborate": "Pourriez-vous m'en dire un peu plus, peut-être avec un exemple concret ?",
        "reasoning": "C'est utile — qu'est-ce qui vous a amené à penser cela, ou quel était votre raisonnement ?",
        "abstract": "Intéressant. Comment pensez-vous que cela pourrait être différent dans une autre situation, ou du point de vue de quelqu'un d'autre ?",
    },
}

CLOSING = {
    "en": "Thank you so much for your time. The assessment is now complete. You will be contacted by the recruitment team.",
    "fr": "Merci beaucoup pour votre temps. L'évaluation est maintenant terminée. Vous serez contacté par l'équipe de recrutement.",
}

TERMINATION_MESSAGES = {
    "en": {
        "candidate_refused": "I understand. Since we cannot continue the assessment, I am ending the interview now. Thank you for your time.",
        "abusive_language": "I am ending the interview now because our conversation has not remained respectful. Thank you for your time.",
    },
    "fr": {
        "candidate_refused": "Je comprends. Comme nous ne pouvons pas poursuivre l'évaluation, je mets fin à l'entretien maintenant. Merci pour votre temps.",
        "abusive_language": "Je mets fin à l'entretien maintenant car notre conversation n'est pas restée respectueuse. Merci pour votre temps.",
    },
}

REFUSAL_PATTERNS = {
    "en": ("i don't want to answer", "i refuse", "no answer", "skip this question", "i won't answer", "not answering"),
    "fr": ("je ne veux pas répondre", "je refuse", "passer cette question", "je ne répondrai pas", "je ne réponds pas"),
}

ABUSIVE_WORDS = {
    "en": ("fuck you", "f*** you", "shut up", "idiot", "stupid", "moron", "bitch", "asshole"),
    "fr": ("ferme ta gueule", "ta gueule", "connard", "connasse", "idiot", "idiote", "imbécile"),
}

SEVERE_ABUSE_PATTERNS = {
    "en": ("kill you", "i will hurt you", "you should die", "nigger", "faggot"),
    "fr": ("je vais te tuer", "je vais vous tuer", "tu devrais mourir"),
}

MAX_INTERVIEW_SECONDS = 720


def classify_candidate_text(text: str, language: str) -> dict:
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    severe = any(pattern in normalized for pattern in SEVERE_ABUSE_PATTERNS[language])
    abusive = severe or any(pattern in normalized for pattern in ABUSIVE_WORDS[language])
    refusal = any(pattern in normalized for pattern in REFUSAL_PATTERNS[language])
    return {"abusive": abusive, "severe": severe, "refusal": refusal}


async def run_sarah_pipeline(
    websocket,
    token: str,
    language: str,
    audio_path: str,
    *,
    candidate_name: str = "Candidate",
    resume_transcript: list[dict] | None = None,
    resume_duration_seconds: float = 0.0,
):
    """Run the interview over the WebSocket. Gemini Live is the only interview-conduct
    implementation in this project; see gemini_live.py for the actual pipeline.

    resume_transcript/resume_duration_seconds carry forward whatever a prior,
    recoverably-dropped attempt on this same interview already captured (see
    api/routes/session.py) -- Gemini Live itself has no memory across a fresh
    client.aio.live.connect() call, so this is what makes "transcript already
    captured is preserved" true across a reconnect without pretending the candidate's
    actual conversation with Sarah continued uninterrupted (it starts over; only the
    record of what was already said is carried into the final evaluated transcript).
    """
    from backend.services.gemini_live import run_gemini_live_pipeline
    return await run_gemini_live_pipeline(
        websocket, token, language, audio_path, candidate_name=candidate_name,
        resume_transcript=resume_transcript, resume_duration_seconds=resume_duration_seconds,
    )

import sqlite3
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from backend import database
from backend.api.deps import get_db, validate_interview_start, validate_interview_token
from backend.core.config import settings

router = APIRouter()


def _build_email_en(name: str, url: str) -> str:
    return f"""Hello {name},

You have been invited to complete a spoken English language assessment with VoiceHR.

WHAT THIS IS
VoiceHR is an AI-powered language assessment tool. You will have a short spoken conversation with Sarah, an AI interviewer, who will guide you through 4 tasks over roughly 10 to 12 minutes. The goal is to evaluate how fluently and effectively you communicate in English.

HOW TO PERFORM WELL
- Speak as fluently and naturally as possible.
- Use professional vocabulary where appropriate.
- Elaborate your answers — give examples, explain your reasoning, and develop your points. Short answers will score lower than detailed ones.
- There are no trick questions. The goal is natural, confident communication.

WHAT YOU NEED
- A quiet environment with minimal background noise.
- A working microphone (headset recommended).
- A modern browser (Chrome or Edge preferred).

YOUR INTERVIEW LINK
{url}

This link is private and expires in 7 days. Do not share it.

IMPORTANT
Your result will be shared with the recruiter. It will not be shown to you directly.

If you experience a technical problem during the interview, you will be given one opportunity to reconnect and continue.

Good luck,
The VoiceHR Team
"""


def _build_email_fr(name: str, url: str) -> str:
    return f"""Bonjour {name},

Vous avez été invité(e) à passer une évaluation de langue française avec VoiceHR.

DE QUOI S'AGIT-IL ?
VoiceHR est un outil d'évaluation linguistique basé sur l'intelligence artificielle. Vous aurez une courte conversation parlée avec Sarah, une intervieweuse IA, qui vous guidera à travers 4 tâches en environ 10 à 12 minutes. L'objectif est d'évaluer la fluidité et l'efficacité de votre communication en français.

COMMENT BIEN PERFORMER
- Parlez de manière aussi fluide et naturelle que possible.
- Utilisez un vocabulaire professionnel lorsque c'est approprié.
- Développez vos réponses — donnez des exemples, expliquez votre raisonnement et approfondissez vos points. Les réponses courtes obtiendront un score inférieur aux réponses développées.
- Il n'y a pas de questions pièges. L'objectif est une communication naturelle et confiante.

CE DONT VOUS AVEZ BESOIN
- Un environnement calme avec peu de bruit de fond.
- Un microphone fonctionnel (casque recommandé).
- Un navigateur moderne (Chrome ou Edge de préférence).

VOTRE LIEN D'ENTRETIEN
{url}

Ce lien est privé et expire dans 7 jours. Ne le partagez pas.

IMPORTANT
Votre résultat sera communiqué au recruteur. Il ne vous sera pas montré directement.

Si vous rencontrez un problème technique pendant l'entretien, vous aurez une opportunité de vous reconnecter et de continuer.

Bonne chance,
L'équipe VoiceHR
"""


async def _send_email(to: str, subject: str, body: str):
    api_key = getattr(settings, "resend_api_key", None)
    admin_email = getattr(settings, "admin_email", None)
    if not api_key:
        raise HTTPException(status_code=500, detail="Email delivery not configured. Set RESEND_API_KEY in .env.")

    import httpx

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": f"VoiceHR <{admin_email or 'noreply@voicehr.app'}>",
                "to": [to],
                "subject": subject,
                "text": body,
            },
        )
        if response.status_code not in (200, 201):
            raise HTTPException(status_code=502, detail=f"Email send failed: {response.text}")


def _get_invitation_content(row, token: str):
    interview_url = f"{getattr(settings, 'base_url', 'http://localhost:8000')}/interview?token={token}"
    if row["language"] == "fr":
        return "Votre évaluation linguistique VoiceHR", _build_email_fr(row["candidate_name"], interview_url)
    return "Your VoiceHR Language Assessment", _build_email_en(row["candidate_name"], interview_url)


@router.get("/interview/{token}/invite/preview")
def preview_invitation_email(token: str):
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM interviews WHERE token = ?", (token,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Interview not found")
        subject, body = _get_invitation_content(row, token)
        return {"subject": subject, "body": body}
    finally:
        conn.close()


@router.post("/interview/{token}/invite")
async def send_invitation_email(token: str):
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM interviews WHERE token = ?", (token,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Interview not found")

        subject, body = _get_invitation_content(row, token)

        await _send_email(row["candidate_email"], subject, body)
        conn.execute("UPDATE interviews SET invite_sent = 1 WHERE token = ?", (token,))
        conn.commit()
        return {"status": "sent"}
    finally:
        conn.close()


@router.get("/interview/{token}")
def get_interview(token: str, row=Depends(validate_interview_token)):
    return {"candidate_name": row["candidate_name"], "language": row["language"]}


@router.post("/interview/{token}/start")
def start_interview(request: Request, token: str, row=Depends(validate_interview_start), db=Depends(get_db)):
    db.execute(
        "UPDATE interviews SET status = 'active', started_at = ? "
        "WHERE token = ? AND status = 'pending'",
        (datetime.utcnow().isoformat(), token),
    )
    if db.execute("SELECT changes()").fetchone()[0] != 1:
        raise HTTPException(status_code=409, detail="Interview already completed or active")
    db.commit()

    websocket_scheme = "wss" if request.url.scheme == "https" else "ws"
    return {
        "ws_url": f"{websocket_scheme}://{request.url.netloc}/ws/interview/{token}",
        "live_audio": bool(getattr(settings, "gemini_api_key", None)),
    }


@router.post("/interview/{token}/prepare")
async def prepare_interview(token: str, row=Depends(validate_interview_token)):
    """Gemini Live needs no separate warm-up step -- it connects lazily when the
    WebSocket session starts. This just confirms the token is still valid to begin."""
    return {"status": "ready", "language": row["language"], "live": True}


@router.post("/interview/{token}/complete")
def complete_interview(token: str, db=Depends(get_db)):
    row = db.execute("SELECT status FROM interviews WHERE token = ?", (token,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Invalid token")
    if row["status"] not in ("active", "completed", "inconclusive"):
        raise HTTPException(status_code=409, detail="Interview has not started")

    if row["status"] == "completed":
        return {"status": "completed"}

    db.execute(
        "UPDATE interviews SET status = 'completed', completed_at = ? WHERE token = ?",
        (datetime.utcnow().isoformat(), token),
    )
    db.commit()
    return {"status": "completed"}

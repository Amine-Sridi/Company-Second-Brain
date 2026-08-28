from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from backend.api.deps import require_admin_auth
from backend.database import init_db
from backend.api.routes import invite, interview, report, session

app = FastAPI(title="VoiceHR")


@app.get("/health")
def health():
    """Cheap liveness check for container healthchecks. Richer diagnostics (DB, Ollama,
    which evaluator is configured) live at GET /health/detail (report.py) instead of
    here, so this stays a single trivial request."""
    return {"status": "ok", "service": "voicehr"}


@app.on_event("startup")
def startup():
    init_db()

app.include_router(invite.router, prefix="/api")
app.include_router(interview.router, prefix="/api")
app.include_router(session.router)
app.include_router(report.router)

# StaticFiles serves directory indexes, not files named admin.html or
# interview.html. Keep the public page URLs aligned with the documentation.
@app.get("/admin", include_in_schema=False)
def admin_page(_auth: None = Depends(require_admin_auth)):
    return FileResponse("frontend/admin.html")


@app.get("/", include_in_schema=False)
def root_page():
    return RedirectResponse(url="/admin")


@app.get("/interview", include_in_schema=False)
def interview_page():
    return FileResponse("frontend/interview.html")


@app.get("/report", include_in_schema=False)
def report_page():
    return FileResponse("frontend/report.html")

app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")

from fastapi import FastAPI

app = FastAPI(title="Resume Intelligence")


@app.get("/health")
def health():
    return {"status": "ok", "service": "resume-intelligence"}

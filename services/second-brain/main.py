from fastapi import FastAPI

app = FastAPI(title="Second Brain")


@app.get("/health")
def health():
    return {"status": "ok", "service": "second-brain"}

from fastapi import FastAPI

app = FastAPI(title="Recruitment Workflow Service")


@app.get("/health")
def health():
    return {"status": "ok", "service": "recruitment-workflow"}

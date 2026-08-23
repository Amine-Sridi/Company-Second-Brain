FROM python:3.12-slim

# Install system deps needed by the extraction pipeline
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Build context is the project root; paths below are relative to it.
COPY services/resume-intelligence/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Shared cross-service Pydantic contracts
COPY shared/ /app/shared/

# Service source (comes last so code changes don't bust the pip cache)
COPY services/resume-intelligence/ /app/

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]

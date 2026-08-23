FROM python:3.12-slim

WORKDIR /app

# Build context is the project root; paths below are relative to it.
COPY services/second-brain/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Shared cross-service Pydantic contracts
COPY shared/ /app/shared/

# Service source
COPY services/second-brain/ /app/

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

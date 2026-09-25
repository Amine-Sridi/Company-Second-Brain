FROM python:3.12-slim

WORKDIR /app

# Build context is the project root; paths below are relative to it.
COPY services/second-brain/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Shared cross-service Pydantic contracts
COPY shared/ /app/shared/

# Service source
COPY services/second-brain/ /app/

# Schema first, then serve. Migrations were previously never run by anything: a
# clean-volume `docker compose up` produced containers that reported healthy
# (/health does not touch the DB) while every real request 500'd, until someone
# manually ran `alembic upgrade head` in each of the three services. Doing it here
# means the image cannot serve a schema it was not built for.
#
# `exec` so uvicorn replaces the shell and still receives SIGTERM directly on
# `docker stop`. A failed migration exits non-zero and the container stops rather
# than serving against a wrong schema -- with `restart: unless-stopped` that shows
# up as a visible crash loop, which is the intended, honest failure mode.
# Safe without locking: one replica per service, each owning its own schema.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn main:app --host 0.0.0.0 --port 8000"]

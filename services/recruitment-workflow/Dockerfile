# Build context is the project root (see infra/docker-compose.yml); paths below are
# relative to it.

# ---------------------------------------------------------------------------
# Stage 1: build the React frontend. Node exists only in this stage -- it never
# reaches the runtime image (see FRONTEND_HANDOFF.md §17).
# ---------------------------------------------------------------------------
FROM node:20-slim AS frontend-build
WORKDIR /frontend
COPY services/recruitment-workflow/frontend/package*.json ./
RUN npm ci
COPY services/recruitment-workflow/frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: the actual service image. Only the frontend's built static output
# (frontend/dist/) is copied in from stage 1 -- no Node, no node_modules, no
# frontend source.
# ---------------------------------------------------------------------------
FROM python:3.12-slim

WORKDIR /app

COPY services/recruitment-workflow/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Shared cross-service Pydantic contracts
COPY shared/ /app/shared/

# Service source. This also copies services/recruitment-workflow/frontend/ (its
# unbuilt JS/JSX source, since the whole service directory is copied as one tree) --
# removed immediately below and replaced with only the built dist/ output from stage
# 1, so the runtime image never carries frontend source, node_modules, or Node itself.
COPY services/recruitment-workflow/ /app/
RUN rm -rf /app/frontend

COPY --from=frontend-build /frontend/dist /app/frontend/dist

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

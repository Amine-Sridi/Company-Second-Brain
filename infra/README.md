# infra

Everything needed to run the whole platform locally with Docker Compose.

| Path | Purpose |
|---|---|
| `docker-compose.yml` | The eight containers and their volumes |
| `.env.example` | Variables read by Compose itself (copy to `.env` in this folder) |
| `postgres/init/01_schemas.sql` | Creates the three schemas: `resume_intel`, `second_brain`, `recruitment` |
| `postgres/init/02_extensions.sql` | Enables `pgvector` |
| `postgres/init/03_roles_and_grants.sh` | Creates three least-privilege roles, one per schema, with no cross-schema grants |

The init scripts run once, the first time the Postgres volume is created.

## Containers

| Container | Host port (default) | Role |
|---|---|---|
| `csb-postgres` | 5432 | One PostgreSQL instance with pgvector and the three schemas |
| `csb-redis` | none | Queue broker for resume ingestion only (never stores personal data) |
| `csb-resume-intelligence` | none (internal) | Resume upload API and contact lookup |
| `csb-resume-intelligence-worker` | none | Background resume extraction (ARQ) |
| `csb-second-brain` | none (internal) | Candidate knowledge and semantic retrieval |
| `csb-recruitment-workflow` | 8003 | Workflow API and the recruiter interface |
| `csb-voicehr` | 8000 | Candidate interview page and language evaluation |
| `csb-ollama` | 11434 | Optional local model server, a fallback for selected tasks |

Containers always listen on 8000 internally. Only Postgres, the workflow service, VoiceHR and Ollama are
published to the host. Resume Intelligence owns the candidate personal data and Second Brain is only
called by the workflow service, so both stay on the Compose network; reach them for debugging with
`docker exec`. The browser talks to the workflow service (port 8003), and candidates open their
interview link on VoiceHR (port 8000).

## Run

```bash
cp infra/.env.example infra/.env
for s in resume-intelligence second-brain recruitment-workflow voicehr; do
  cp services/$s/.env.example services/$s/.env
done
# edit the .env files: set the API keys and secrets (the role passwords in infra/.env must
# match the passwords inside each service's DATABASE_URL)

docker compose -f infra/docker-compose.yml up -d --build
docker compose -f infra/docker-compose.yml ps
```

`csb-ollama` reserves an NVIDIA GPU and bind-mounts a host model folder (`OLLAMA_MODELS_DIR`).
It is not needed to start the platform. On a machine without an NVIDIA runtime, start the other
services only:

```bash
docker compose -f infra/docker-compose.yml up -d --build \
  postgres redis resume-intelligence resume-intelligence-worker second-brain recruitment-workflow voicehr
```

A changed `.env` needs `docker compose up -d` to take effect: `docker compose restart` reuses the
environment the container was created with.

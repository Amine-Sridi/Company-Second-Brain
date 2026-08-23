#!/usr/bin/env bash
# Company Second Brain: least-privilege roles (Step 3)
#
# One role per service, each scoped to exactly its own schema. "No cross-schema grants"
# means literally that: resume_intel_role has nothing on second_brain or recruitment, and
# so on for the other two. Services can only reach each other's data through the HTTP APIs,
# never by querying another service's tables directly, even though they share one Postgres
# instance.
#
# Role passwords come from environment variables (see infra/.env.example) rather than being
# hardcoded here, matching how service-to-service API keys are handled elsewhere in this
# project. They must match the password portion of the corresponding service's DATABASE_URL.
set -euo pipefail

: "${RESUME_INTEL_ROLE_PASSWORD:?RESUME_INTEL_ROLE_PASSWORD must be set}"
: "${SECOND_BRAIN_ROLE_PASSWORD:?SECOND_BRAIN_ROLE_PASSWORD must be set}"
: "${RECRUITMENT_ROLE_PASSWORD:?RECRUITMENT_ROLE_PASSWORD must be set}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    -- resume_intel_role: owns candidate PII, nothing outside resume_intel
    CREATE ROLE resume_intel_role LOGIN PASSWORD '$RESUME_INTEL_ROLE_PASSWORD';
    GRANT USAGE, CREATE ON SCHEMA resume_intel TO resume_intel_role;
    ALTER ROLE resume_intel_role SET search_path = resume_intel;
    REVOKE ALL ON SCHEMA public FROM resume_intel_role;

    -- second_brain_role: owns PII-free candidate/company knowledge + embeddings, nothing
    -- outside second_brain
    CREATE ROLE second_brain_role LOGIN PASSWORD '$SECOND_BRAIN_ROLE_PASSWORD';
    GRANT USAGE, CREATE ON SCHEMA second_brain TO second_brain_role;
    ALTER ROLE second_brain_role SET search_path = second_brain;
    REVOKE ALL ON SCHEMA public FROM second_brain_role;

    -- recruitment_role: owns jobs/matching/workflow/decisions, nothing outside recruitment
    CREATE ROLE recruitment_role LOGIN PASSWORD '$RECRUITMENT_ROLE_PASSWORD';
    GRANT USAGE, CREATE ON SCHEMA recruitment TO recruitment_role;
    ALTER ROLE recruitment_role SET search_path = recruitment;
    REVOKE ALL ON SCHEMA public FROM recruitment_role;
EOSQL

"""jobs, job_descriptions, scoring_configs, match_results

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-24

Core RWS tables for JD structuring + hybrid matching. match_results is immutable --
no update path exists anywhere in application code, only inserts. A default
scoring_configs row (version 1) is seeded so matching has a config to use immediately.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE recruitment.jobs (
            id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            title       TEXT        NOT NULL,
            threshold   FLOAT       NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT jobs_threshold_range CHECK (threshold >= 0.0 AND threshold <= 1.0)
        )
    """)

    op.execute("""
        CREATE TABLE recruitment.job_descriptions (
            id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            job_id                UUID        NOT NULL REFERENCES recruitment.jobs(id),
            version               INTEGER     NOT NULL,
            raw_text              TEXT        NOT NULL,
            required_skills       JSONB       NOT NULL DEFAULT '[]',
            preferred_skills      JSONB       NOT NULL DEFAULT '[]',
            min_years_experience  FLOAT,
            titles                JSONB       NOT NULL DEFAULT '[]',
            structuring_model     TEXT        NOT NULL,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT job_descriptions_job_version_unique UNIQUE (job_id, version)
        )
    """)

    op.execute("""
        CREATE TABLE recruitment.scoring_configs (
            id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            version              INTEGER     NOT NULL UNIQUE,
            weight_semantic      FLOAT       NOT NULL,
            weight_deterministic FLOAT       NOT NULL,
            skill_weight         FLOAT       NOT NULL,
            experience_weight    FLOAT       NOT NULL,
            title_weight         FLOAT       NOT NULL,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT scoring_configs_hybrid_weights_sum_to_one
                CHECK (ABS(weight_semantic + weight_deterministic - 1.0) < 0.0001),
            CONSTRAINT scoring_configs_component_weights_sum_to_one
                CHECK (ABS(skill_weight + experience_weight + title_weight - 1.0) < 0.0001)
        )
    """)

    op.execute("""
        CREATE TABLE recruitment.match_results (
            id                        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            job_id                    UUID        NOT NULL REFERENCES recruitment.jobs(id),
            job_description_id        UUID        NOT NULL
                REFERENCES recruitment.job_descriptions(id),
            job_description_version   INTEGER     NOT NULL,
            scoring_config_id         UUID        NOT NULL
                REFERENCES recruitment.scoring_configs(id),
            scoring_config_version    INTEGER     NOT NULL,
            embedding_model_version   TEXT        NOT NULL,
            candidate_id              UUID        NOT NULL,
            candidate_snapshot        JSONB       NOT NULL,
            threshold_snapshot        FLOAT       NOT NULL,
            semantic_score            FLOAT       NOT NULL,
            skill_score               FLOAT       NOT NULL,
            experience_score          FLOAT       NOT NULL,
            title_score               FLOAT       NOT NULL,
            deterministic_score       FLOAT       NOT NULL,
            final_score               FLOAT       NOT NULL,
            hard_gate_passed          BOOLEAN     NOT NULL,
            matched_requirements      JSONB       NOT NULL,
            missing_requirements      JSONB       NOT NULL,
            qualified                 BOOLEAN     NOT NULL,
            created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX match_results_job_id_idx ON recruitment.match_results (job_id)
    """)

    # Default scoring config: equal weight to semantic and deterministic signals;
    # within the deterministic side, skills matter most, then experience, then title.
    op.execute("""
        INSERT INTO recruitment.scoring_configs
            (version, weight_semantic, weight_deterministic,
             skill_weight, experience_weight, title_weight)
        VALUES (1, 0.5, 0.5, 0.6, 0.2, 0.2)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS recruitment.match_results")
    op.execute("DROP TABLE IF EXISTS recruitment.scoring_configs")
    op.execute("DROP TABLE IF EXISTS recruitment.job_descriptions")
    op.execute("DROP TABLE IF EXISTS recruitment.jobs")

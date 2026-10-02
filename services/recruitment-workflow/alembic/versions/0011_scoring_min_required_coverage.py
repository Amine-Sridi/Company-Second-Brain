"""scoring_configs: min_required_coverage, and a v2 config that uses it

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-02

Makes the required-skills hard gate a coverage floor instead of an all-or-nothing
check, and versions that floor the same way every scoring weight is already versioned.

WHY IT BELONGS ON scoring_configs AND NOT IN CODE
Every MatchResult snapshots scoring_config_version precisely so a past decision stays
reproducible from what was stored. A gate threshold living as a module constant could be
changed between two runs and leave two MatchResults that disagree with no record of why --
which breaks the same guarantee as quietly editing a weight would. So it is a column, it
is versioned, and an existing MatchResult keeps pointing at the config that produced it.

WHY THE DEFAULT IS 1.0 AND v1 IS LEFT ALONE
1.0 is the behaviour that already exists: every required skill must be present. Backfilling
v1 with anything looser would retroactively change what "scoring_config_version = 1" means,
so every MatchResult ever scored under it would cite a config that no longer describes how
it was scored. v1 therefore keeps the strict gate, and a new v2 row carries the looser one.
Nothing in the past is reinterpreted; new submissions simply use the newest config.

WHY 0.8
A judgment call, documented as one rather than presented as measured. With required_skills
now capped at 6 (jd_validation.MAX_REQUIRED_SKILLS), 0.8 tolerates one miss out of five or
six and still rejects two. The case that motivated it: a consultant with fifteen years of
Power BI, SQL Server, SSAS, SSIS and Azure Synapse was disqualified for not literally
listing "SQL" -- one unmatched token out of three. Whether 0.8 is the right number for a
real hiring process is exactly the sort of thing to tune against outcomes; what matters
architecturally is that changing it now requires a new version, visible on every result.
"""
from alembic import op
import sqlalchemy as sa


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default is set so the column can be NOT NULL without a separate backfill:
    # every existing row (v1) takes 1.0, which is the gate those rows were scored under.
    op.add_column(
        "scoring_configs",
        sa.Column(
            "min_required_coverage",
            sa.Float(),
            nullable=False,
            server_default="1.0",
        ),
        schema="recruitment",
    )

    # The new default config. Inserted rather than updating v1, for the reason in the
    # docstring: v1 must keep describing how v1-scored results were scored.
    #
    # The weights are copied from v1 unchanged -- this migration changes the gate and
    # nothing else, so a before/after comparison isolates one variable.
    op.execute(
        """
        INSERT INTO recruitment.scoring_configs
            (version, weight_semantic, weight_deterministic,
             skill_weight, experience_weight, title_weight, min_required_coverage)
        SELECT 2, weight_semantic, weight_deterministic,
               skill_weight, experience_weight, title_weight, 0.8
          FROM recruitment.scoring_configs
         WHERE version = 1
           AND NOT EXISTS (
               SELECT 1 FROM recruitment.scoring_configs WHERE version = 2
           )
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM recruitment.scoring_configs WHERE version = 2")
    op.drop_column("scoring_configs", "min_required_coverage", schema="recruitment")

-- Company Second Brain: schema creation (Step 3)
--
-- One Postgres instance, three schemas, one per bounded-context service. Roles and grants
-- are defined separately in 03_roles_and_grants.sh so role passwords come from environment
-- variables rather than being hardcoded in a checked-in SQL file.

CREATE SCHEMA IF NOT EXISTS resume_intel;
CREATE SCHEMA IF NOT EXISTS second_brain;
CREATE SCHEMA IF NOT EXISTS recruitment;

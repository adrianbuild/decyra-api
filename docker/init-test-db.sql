-- Runs once on first container startup. Creates the test database
-- alongside the dev database (decyra). pgvector EXTENSION + schema
-- arrive in Task 1.3 via alembic.
CREATE DATABASE decyra_test;

-- Task 2.2c: give the unprivileged app role a LOCAL DEV password so the
-- app can direct-connect as decyra_app (DATABASE_URL). The role's
-- attributes + grants are created by the 2.2c migration; this only seeds
-- a throwaway dev credential.
--
-- !!! LOCAL DEV ONLY — same risk class as the committed postgres:postgres
-- !!! credentials above. NEVER use in production. Prod sets the
-- !!! decyra_app password from infra/secrets (Task 8.1).
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'decyra_app') THEN
        CREATE ROLE decyra_app LOGIN NOSUPERUSER NOBYPASSRLS;
    END IF;
END
$$;
ALTER ROLE decyra_app LOGIN PASSWORD 'decyra_app_dev';

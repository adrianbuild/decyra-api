-- Runs once on first container startup. Creates the test database
-- alongside the dev database (decyra). pgvector EXTENSION + schema
-- arrive in Task 1.3 via alembic.
CREATE DATABASE decyra_test;

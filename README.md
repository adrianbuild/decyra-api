# decyra-api

FastAPI backend for Decyra — the EU-sovereign AI platform for the regulated German Mittelstand.

**Stack:** Python 3.11, FastAPI, LiteLLM, SQLAlchemy, Alembic.

## Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in real values, never commit .env
```

## Database (local)

```bash
docker compose up -d
docker compose ps   # wait until postgres is "healthy"
```

Runs Postgres 17 with pgvector preinstalled on port 55432 with two
databases: `decyra` (dev) and `decyra_test` (test). Schema arrives in
Task 1.3.

## Run

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
# → GET http://localhost:8000/health  →  {"status": "ok"}
```

## Test

```bash
source .venv/bin/activate
pytest
```

Test fixtures bind to `decyra_test` and refuse to run against any
other database — strict isolation from dev data. The `engine` fixture
verifies the connection at session start; per-test transaction
rollback arrives once the schema lands in Task 1.3.

## Docker

```bash
docker build -t decyra-api .
docker run --env-file .env -p 8000:8000 decyra-api
```

## Source of truth

Project-wide context, rules and roadmap live in `/Users/adrian/PROJECT/`:
- `CLAUDE.md` — project context for Claude Code
- `WORKPLAN.md` — task-by-task plan
- `PROGRESS.md` — current status

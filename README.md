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

## Run

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
# → GET http://localhost:8000/health  →  {"status": "ok"}
```

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

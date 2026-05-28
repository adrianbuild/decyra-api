# PROGRESS.md — Decyra final

> Aktueller Stand. Claude Code aktualisiert das nach jeder Session. Vor jeder neuen Session zuerst lesen.

## Aktueller Task
**Block 1 — Projekt-Setup**: Tasks 1.1, 1.2 und 1.3 abgeschlossen → als nächstes Task 2.1 (Supabase Auth).

## Status der Task-Blöcke
- [~] Block 0 — Voraussetzungen (0.2 lokale Umgebung erledigt: Node 20 via nvm, Python 3.11, Docker; 0.1 Accounts/Keys parallel)
- [x] Block 1 — Projekt-Setup ([x] 1.1 Repos, [x] 1.2 Tests, [x] 1.3 DB-Schema)
- [ ] Block 2 — Auth & Multi-Tenant (2.1 Auth, 2.2 Workspace, 2.3 Einladungen, 2.4 Isolation-Test)
- [ ] Block 3 — Audit-Log (3.1 Hash-Chain, 3.2 Verify & async)
- [ ] Block 4 — Routing/Chat/PII (4.1 LiteLLM, 4.2 Test-Frontend, 4.3 Chat-Proxy, 4.4 Streaming, 4.5 PII, 4.6 Fehler/Fallback)
- [ ] Block 5 — RAG (5.1 Upload, 5.2 Embeddings, 5.3 Retrieval)
- [ ] Block 5B — Chat-Features (5B.1 Datei-Upload, 5B.2 Datenanalyse+Charts, 5B.3 Vision, 5B.4 Bildgen, 5B.5 Prompt Library, 5B.6 Projects)
- [ ] Block 6 — Frontend (6.1 Chat, 6.2 Dashboard Logs, 6.3 Dashboard Verwaltung)
- [ ] Block 7 — Extension (7.1 Grundgerüst, 7.2 ChatGPT-Integration)
- [ ] Block 8 — Deployment & Pilot (8.1 Deploy, 8.2 Doku, 8.3 Go-Live)

## Festlegungen (nicht vergessen)
- Tech-Stack: siehe CLAUDE.md, keine Abweichung ohne Rückfrage
- Cloud-Modelle: GPT-5, GPT-5 Mini, Claude Sonnet 4.6, Claude Haiku 4.5, Gemini 3.5 Flash
- Sovereign-Modelle: Mistral Large 3, Mistral Small (über Mistral La Plateforme, Phase 1)
- Text-Embedding: mistral-embed, 1024 Dimensionen → DB-Spalte vector(1024)
- Chat-Features im MVP: Datei-Upload, Datenanalyse+Charts, Vision, Bildgen, Prompt Library, Projects
- Phase 1.5: Agents, Workflows, Sprache, OneDrive, Memory, Connectoren
- Pflicht-Tests: Hash-Chain (3.2), PII-Routing (4.5), Multi-Tenant-Isolation (2.4), Sandbox-Isolation (5B.2)
- Zeitschätzung: ~20–24 Wochen

## Offene Fragen / Blocker
- Bildgenerierung (5B.4): EU-Provider FLUX/Black Forest Labs — DPA und Souveränität vor Bau klären

## Letzte Session
- 2026-05-28: Task 1.3 abgeschlossen. Datenbank-Schema + Migrations in
  decyra-api komplett:
  - `alembic init alembic` (sync-Template). `alembic.ini` mit leerer
    `sqlalchemy.url`, `alembic/env.py` lädt die URL aus
    `app.config.get_settings()` → eine Quelle für dev (`decyra`) und test
    (`decyra_test`), keine hardcoded URLs.
  - Eine Migration `313c10e517e1_initial_schema.py`:
    - `CREATE EXTENSION vector` (kein pgcrypto, kein uuid-ossp — PG17 hat
      `gen_random_uuid()` built-in).
    - Enum `workspace_role ('owner','admin','user')` per `CREATE TYPE`.
    - 8 Tabellen: organizations, users, workspaces, workspace_members,
      models, audit_events, documents, document_chunks (+ alembic_version
      = 9 sichtbare).
    - `document_chunks.embedding vector(1024)` per `op.execute` (kein
      `pgvector`-Python-Paket, nur DDL).
    - Indexe: `(workspace_id, timestamp DESC)` auf audit_events,
      `workspace_id` auf documents, `workspace_id`+`document_id` auf
      document_chunks.
    - FK-Regeln: workspace_members CASCADE; documents/document_chunks
      CASCADE auf workspace_id; audit_events RESTRICT auf workspace_id
      und user_id (Audit darf nicht verschwinden); uploaded_by RESTRICT.
    - Append-only auf audit_events via PL/pgSQL-Trigger
      (BEFORE UPDATE/DELETE → RAISE EXCEPTION). Trigger statt REVOKE,
      damit auch der Tabellen-Owner geblockt wird.
    - RLS auf workspaces, workspace_members, audit_events, documents,
      document_chunks — `ENABLE` + `FORCE` (sonst bypasst der Owner).
      Policies prüfen `id = current_setting('app.current_workspace_id',
      true)::uuid` bzw. `workspace_id = current_setting(...)`. Default
      = secure: ohne gesetzte GUC liefert current_setting NULL → Policy
      verweigert.
    - organizations, users, models bleiben RLS-frei (nicht
      workspace-skopiert).
  - `tests/conftest.py` erweitert: session-scope dropt+rekonstruiert
    `public` und `vector`-Extension, läuft `command.upgrade(cfg, "head")`,
    legt **`decyra_app`-Role** (NOSUPERUSER, NOBYPASSRLS) mit `GRANT ALL
    ON ALL TABLES IN SCHEMA public` an — denn `postgres` ist Superuser
    und bypasst RLS auch mit FORCE. Sicherheits-Assertion
    (`"decyra_test" in database_url`) bleibt unverändert.
  - Neue `db`-Fixture (function-scope): Connection + Transaction, am
    Test-Ende Rollback. Keine State-Bleed zwischen Tests.
  - `tests/test_schema.py` mit 6 Verifikations-Tests:
    `test_all_tables_exist`, `test_pgvector_installed`,
    `test_document_chunks_embedding_is_vector_1024`,
    `test_audit_events_rejects_update` und `…rejects_delete` (mit
    SAVEPOINT-Pattern, damit die abgebrochene Transaktion lokal
    aufgefangen wird), `test_workspace_id_isolation_via_rls` (zwei
    Workspaces, SET LOCAL ROLE decyra_app, beweist dass A nur
    `['a.pdf']` sieht und B nur `['b.pdf']`).
  - `pytest -v`: 7/7 grün (test_health + die 6 neuen).
  - Migration ausgeführt gegen `decyra_test` und `decyra` — beide DBs
    zeigen 9 Tabellen, vector-Extension, beide append-only-Trigger und
    `embedding vector(1024)`.

- 2026-05-28: Task 1.2 abgeschlossen. Test-Infrastruktur in decyra-api:
  - docker-compose.yml mit `pgvector/pgvector:pg17` auf Port 55432
    (vermeidet Kollision mit dem laufenden `supabase_db_mvp` auf 54322).
    Container heißt `decyra-postgres`, persistentes Volume `decyra_pgdata`.
    Init-Script `docker/init-test-db.sql` legt beim ersten Start
    `CREATE DATABASE decyra_test;` neben der Default-DB `decyra` an.
    Healthcheck auf `pg_isready`.
  - pyproject.toml mit `pytest-asyncio` "auto" mode, `testpaths=["tests"]`,
    `pythonpath=["."]` — pytest findet die App ohne Editable-Install.
  - tests/conftest.py: `TEST_DATABASE_URL`-Override VOR `from app...`
    Import (sonst greift `@lru_cache` von `get_settings()` auf falscher
    URL). Session-scoped SQLAlchemy-Engine mit Sicherheits-Assertion
    (`"decyra_test" in database_url` — wenn nicht, raise). Per-test
    httpx AsyncClient via `ASGITransport(app=app)` — kein TCP-Roundtrip.
  - tests/test_health.py grün: GET /health → 200 / {"status":"ok"}.
  - requirements.txt: `pytest-asyncio>=0.24,<2.0` ergänzt; .env.example:
    Port 5432 → 55432.
  - .dockerignore hält tests/ und docker/ aus dem Production-Image fern.
  - README dokumentiert `docker compose up -d` + `pytest`-Workflow.
  - CI vertagt (kein Remote). Verifikation: pytest grün, beide DBs im
    Container nachgewiesen via `psql -l`, engine-Fixture-Logik via
    Out-of-band Sanity-Check bestätigt.

- 2026-05-28: Task 1.1 abgeschlossen. Drei lokale Repos angelegt:
  - `decyra-api` (FastAPI 0.136, Python 3.11, uvicorn, LiteLLM, SQLAlchemy,
    Alembic, psycopg2-binary, pydantic-settings) — `GET /health` antwortet
    `{"status":"ok"}`, Dockerfile + `.env.example` (Provider-Keys ohne Google
    optional) + leere `alembic/`/`tests/`-Ordner für Task 1.2/1.3.
  - `decyra-web` (Next.js 15.5.18, React 19, TS strict, Tailwind 4,
    shadcn/ui defaults preset/radix-base/css-vars, Button-Komponente)
    — Platzhalter-Startseite, `npm run dev` + `npm run build` durch.
  - `decyra-extension` (Vite 8 + React 19 + TS, MV3 via
    `@crxjs/vite-plugin@2.4.0`) — Popup-Skelett, `npm run build` erzeugt
    valides `dist/manifest.json`.
  - Pro Repo: README, .gitignore (mit `.env`-Ausschluss + `.env.example`-
    Whitelist), `.env.example` ohne echte Werte, `.nvmrc` (Node 20).
  - Drei separate git-Repos, alle auf `main`, je ein Conventional-Commit
    nach Secret-Scan. Kein Remote (lokal-only wie geplant).
  - Vorab-Check Task 0.2 bestätigt: Node 20 via nvm, Python 3.11 via brew,
    Docker Desktop läuft. PostgreSQL nutzen wir später per Docker (Task 1.3).
  - Plan-Abweichung: shadcn-CLI Anfang 2026 umgebaut — keine
    `--style=new-york`/`--base-color=slate`-Flags mehr; stattdessen
    `--defaults --base radix` (preset `base-nova`, css-vars). Theme bleibt
    austauschbar via globals.css.

## Nächster Schritt
Task 2.1 — Supabase Auth (decyra-api + decyra-web):
- Supabase-Client im Backend und Frontend einrichten
- Email-Registrierung mit Bestätigungs-Mail
- Login (Magic Link bevorzugt)
- Auth-Middleware Backend: JWT validieren, user_id extrahieren
- Frontend: Session-Context, geschützte Routen, Logout
- **Wichtig aus 1.3:** App-Tier muss als **non-superuser** Role gegen
  Postgres laufen (sonst greift RLS nicht). In dev/test heißt die
  Rolle `decyra_app` (vom conftest angelegt). Für die echte App-
  Connection im Backend: separater Postgres-User mit `NOSUPERUSER
  NOBYPASSRLS`, der vor jedem Request `SET LOCAL
  app.current_workspace_id = '<uuid>'` setzt.
- Audit-Hash-Berechnung (`prev_hash`/`current_hash`) kommt erst in 3.1
  — die Spalten existieren bereits.

Start in nächster Session:
"Lies WORKPLAN.md und PROGRESS.md. Wir machen Task 2.1. Geh in Plan Mode."

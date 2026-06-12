# WORKPLAN.md — Decyra final (vollständig, fein unterteilt)

> Session-für-Session-Plan für Claude Code. Jeder Task hat konkrete Unterschritte und eine Definition of Done (DoD). Vor jeder Session: diese Datei + PROGRESS.md lesen. Nach jeder Session: PROGRESS.md aktualisieren + committen.

## Nutzung mit Claude Code
1. Neue Session → Claude liest CLAUDE.md automatisch
2. "Lies WORKPLAN.md und PROGRESS.md. Wir machen Task X.Y. Geh in Plan Mode."
3. Claude zeigt Plan → du bestätigst → Claude baut
4. Am Ende: "Aktualisiere PROGRESS.md" + git commit (Conventional Commits)
5. Bei ~60% Kontext: PROGRESS.md updaten, /clear, neue Session
6. Ein Task pro Session. Reihenfolge bindend.

## Wichtige Festlegungen (geklärt)
- Text-Embedding: **mistral-embed** (1024 Dimensionen, EU). → DB-Spalte `vector(1024)`
- Sovereign-Inferenz Phase 1: Mistral La Plateforme (kein eigenes GPU-Hosting)
- Cloud-Modelle: GPT-5, GPT-5 Mini, Claude Sonnet 4.6, Claude Haiku 4.5, Gemini 3.5 Flash
- Chat-Features im MVP: Datei-Upload, Datenanalyse+Charts, Vision, Bildgenerierung, Prompt Library, Projects
- Bildgenerierung: EU-Provider (FLUX/Black Forest Labs, Freiburg) — DPA vor Bau klären
- Phase 1.5: Agents, Workflows, Spracheingabe, OneDrive, Memory, Connectoren
- **Zeitschätzung: ~20–24 Wochen** bei ~15 h/Woche (erweiterter MVP-Scope)

---

## TASK-BLOCK 0 — Voraussetzungen (manuell, ohne Claude Code)

> Das macht der Mensch, nicht Claude Code. Muss vor Block 1 erledigt sein.

### Task 0.1 — Accounts & Keys
- [x] OpenAI Business-Account + Zahlungsmethode + API-Key (Key in .env, Calls funktionieren)
- [x] Anthropic API-Konto + API-Key (Key in .env, Calls funktionieren)
- [ ] Google Cloud + Vertex AI EU aktiviert + Service-Account-Key (offen — Vertex-EU-Zugang steht aus, Modell enabled=false)
- [x] Mistral La Plateforme + API-Key (Key in .env, Calls funktionieren)
- [ ] Hetzner Cloud Account (für Block 8, nicht verifizierbar)
- [x] Supabase Projekt angelegt + Keys notiert (Auth läuft end-to-end)
- [ ] DPA bei jedem Provider angefordert und abgelegt (nicht aus Code/DB verifizierbar)

### Task 0.2 — Lokale Umgebung
- [x] Node.js 20, Python 3.11, Docker, Docker Compose installiert (npm build + pytest + Container laufen)
- [x] PostgreSQL 17 lokal oder via Docker lauffähig (decyra-postgres pg17 läuft)
- [ ] GitHub-Org `decyra` + SSH-Key hinterlegt (offen — Repos sind lokal-only, kein Remote)

---

## TASK-BLOCK 1 — Projekt-Setup

### Task 1.1 — Repos & Grundgerüst
- [x] decyra-api: FastAPI-Projekt-Struktur (app/, tests/, alembic/)
- [x] requirements.txt: fastapi, uvicorn, litellm, sqlalchemy, alembic, psycopg2-binary, pydantic-settings, pytest, httpx
- [x] GET /health Endpoint → {"status": "ok"} (test_health grün)
- [x] pydantic-settings für .env-Handling (alle Provider-Keys)
- [x] Dockerfile für Backend
- [x] decyra-web: Next.js 15 (App Router, TS), Tailwind 4, shadcn/ui init, Platzhalter-Startseite
- [x] decyra-extension: Manifest V3 + Vite-Build-Skelett
- [x] Pro Repo: README, .gitignore (Secrets/node_modules/__pycache__/.env), .env.example
- [x] CLAUDE.md, WORKPLAN.md, PROGRESS.md ins decyra-api Root
- **DoD:** alle drei Projekte starten lokal fehlerfrei; /health antwortet ✅

### Task 1.2 — Test-Infrastruktur
- [x] pytest + pytest-asyncio konfiguriert in decyra-api
- [x] conftest.py mit Test-DB-Fixture (separate Test-Datenbank)
- [x] Erster Dummy-Test läuft grün
- [ ] (optional) GitHub Actions Workflow: pytest bei Push (offen — kein Remote)
- **DoD:** `pytest` läuft, mindestens 1 grüner Test ✅ (62 grün)

### Task 1.3 — Datenbank-Schema & Migrations
- [x] Alembic initialisiert, verbunden mit lokaler DB
- [x] pgvector Extension aktivieren (CREATE EXTENSION vector)
- [x] Tabelle organizations (id uuid, name, created_at)
- [x] Tabelle workspaces (id, organization_id FK, name, settings jsonb, created_at)
- [x] Tabelle users (id, email unique, created_at)
- [x] Tabelle workspace_members (workspace_id FK, user_id FK, role enum owner/admin/user, PK zusammengesetzt)
- [x] Tabelle models (name PK, provider, cost_input numeric, cost_output numeric, eu_hosted bool, sovereign_eligible bool, tier_min)
- [x] Tabelle audit_events (id, workspace_id FK, user_id FK, timestamp, model, request_text, response_text, pii_detected bool, routed_to, prev_hash, current_hash) — append-only
- [x] Tabelle documents (id, workspace_id FK, filename, uploaded_by FK, created_at)
- [x] Tabelle document_chunks (id, document_id FK, workspace_id FK, content text, embedding vector(1024), chunk_index)
- [x] Row-Level Security auf allen Tabellen mit workspace_id (live: RLS ENABLE+FORCE auf workspaces/workspace_members/audit_events/documents/document_chunks)
- [x] Migration ausführen, Schema verifizieren (Migration 313c10e517e1, test_schema 6 grün)
- **DoD:** Migration läuft sauber, alle Tabellen + RLS + pgvector vorhanden ✅

---

## TASK-BLOCK 2 — Auth & Multi-Tenant

### Task 2.1 — Supabase Auth
- [x] Supabase-Client im Backend + Frontend einrichten (lib/supabase/{client,server,middleware})
- [ ] Email-Registrierung mit Bestätigungs-Mail (Registrierung läuft; „Confirm email" in Dev bewusst AUS → Bestätigungs-Mail noch nicht scharf, siehe Security-Härtung Punkt 0)
- [x] Login (in 2.2a von Magic Link auf Email/Passwort umgestellt; /auth/callback bleibt für späteren Magic-Link)
- [x] Auth-Middleware Backend: JWT validieren, user_id extrahieren (JWKS/ES256, test_auth 6 grün)
- [x] Frontend: Session-Context, geschützte Routen, Logout (@supabase/ssr httpOnly-Cookies, middleware.ts, LogoutButton)
- **DoD:** User kann sich registrieren, einloggen, ausloggen; geschützte Route blockt ohne Login ✅ (Email-Bestätigung bewusst verschoben, vor Pilot scharf)

### Task 2.2 — Workspace & Onboarding
> In Unter-Tasks gegliedert: 2.2a (Login-UI), 2.2b (Onboarding),
> 2.2c (decyra_app-Rollen-Switch, vor Pilot).

#### Task 2.2a — Login-UI + Email/Passwort ✅ (2026-06-01)
- [x] Login-UI reaktiviert, Magic-Link → Email/Passwort (signUp/signInWithPassword)
- [x] Session via @supabase/ssr (httpOnly-Cookies), Logout, /me-Beweis

#### Task 2.2b — Workspace/Org-Anlage ✅ (2026-06-01)
- [x] POST /onboarding (JWT, idempotent via advisory lock + Membership-Query)
- [x] Erst-Call: users(id=sub)+org+workspace+owner-membership in einer Transaktion
- [x] Zweit-Call: kein Duplikat, bestehenden Workspace zurückgeben
- [x] Membership-Check auf internem Verify-Endpoint (403 Nicht-Member) nachgezogen
- [x] Frontend ruft /onboarding beim Dashboard-Load (best-effort)
- **DoD:** neuer User landet in eigenem Workspace als Owner ✅ (32 Tests grün)

#### Task 2.2c — decyra_app-Rollen-Switch ✅ (2026-06-02)
- [x] App auf decyra_app (NOSUPERUSER/NOBYPASSRLS) umgestellt, RLS aktiv
- [x] MIGRATION_DATABASE_URL (postgres) vs DATABASE_URL (decyra_app)
- [x] GRANTs + onboard_user (SECURITY DEFINER) in Migration; per-Request set_config
- [x] RLS-Beweis-Test (decyra_app, B unsichtbar, is_superuser=off) + Live-Smoke
- **DoD:** RLS feuert zur Laufzeit, Cross-Tenant-Insert/-Read unmöglich ✅ (35 Tests grün)

### Task 2.3 — Einladungen & Rollen ✅ (2026-06-04)
- [x] invitations-Tabelle (org-skopiert: organization_id, email, role, token, invited_by, status, expires_at) + RLS via app.current_organization_id + GRANTs
- [x] Endpoint: User einladen (Owner/Admin), Einladungs-Mail via Mailpit (Token-Link)
- [x] Einladung annehmen implizit über onboard_user beim Login (email-gebunden, kein separater Accept-Endpoint)
- [x] Rollen-Helper require_role + SECURITY-DEFINER-Resolver current_user_membership
- [x] GET /invitations + POST /invitations/{token}/revoke (Owner/Admin)
- **DoD:** Einladung end-to-end, Rollen erzwungen ✅ (49 Tests grün, Mailpit-Live-Smoke)
- Verschoben (war hier gelistet): „Rolle ändern / User deaktivieren" → bei Bedarf in 2.4/späterem Admin-Task; 2.3 deckt Einladen/Beitreten/Revoke ab.

### Task 2.4 — Multi-Tenant-Isolation (Test!)
- [x] Alle Queries gehen über workspace_id-gefilterte Helper (set_workspace_context / set_org_context vor jedem Read/Write; RLS FORCE als Netz darunter)
- [x] Test: User aus Workspace A kann Daten von Workspace B NICHT lesen (test_rls cross-workspace, test_invitations cross-org, test_chat Privatsphäre — alle als decyra_app)
- [x] Test: RLS greift auch bei direktem DB-Zugriff (test_rls.py: SET LOCAL ROLE decyra_app, is_superuser=off, direkte Queries — B unsichtbar)
- **DoD:** Isolations-Tests grün — kein Cross-Tenant-Zugriff möglich ✅ (Hinweis: 2.4 wurde inzident durch die Tests aus 2.2c/2.3/4.3 erfüllt, nicht als eigene Session; die Isolation ist nachweislich getestet)

---

## TASK-BLOCK 3 — Audit-Log mit Hash-Chain

### Task 3.1 — Hash-Chain-Mechanik
- [x] Postgres-Trigger BEFORE INSERT auf audit_events (live: `audit_events_hash_chain_insert`)
- [x] current_hash = SHA256(prev_hash || workspace_id || user_id || timestamp || model || request || response) (Migration 36cbe1faa786, Kanonisierung v1)
- [x] prev_hash = current_hash des letzten Events im selben Workspace (NULL beim ersten) (advisory lock pro Workspace, clock_timestamp-Ordnung)
- [x] Trigger/Permission: UPDATE und DELETE auf audit_events verbieten (live: `audit_events_no_update` + `audit_events_no_delete`)
- **DoD:** INSERT erzeugt korrekt verkettete Hashes; UPDATE/DELETE schlägt fehl ✅ (test_hash_chain 4 grün inkl. Manipulations-Pflichttest; in 4.3 via verify_workspace_chain re-verifiziert)

### Task 3.2 — Verify & async Write
- [x] verify_chain(workspace_id)-Funktion: liest Events, rechnet Kette nach, gibt OK/Fehlerposition
- [ ] ~~Audit-Write async (BullMQ/Redis-Queue oder FastAPI BackgroundTask)~~ → **verschoben nach 4.3 (Chat-Proxy-Endpoint)**. Dort lebt der erste echte audit_events-Producer; Entscheidung BackgroundTask vs. Queue (Celery/Dramatiq/arq+Redis) fällt basierend auf der echten Pipeline.
- [x] Test: intakte Kette verifiziert OK
- [x] Test: manuell manipulierte Zeile wird an korrekter Position erkannt
- [x] Bonus: Public Verify-Endpoint `GET /v/{token}` mit HS256-Token (eigenes `AUDIT_VERIFY_SECRET`, 30d-Default-TTL, UUID-sub-Guard)
- **DoD:** beide Verify-Tests grün (intakt + manipuliert); async-Write zu 4.3 verschoben

---

## TASK-BLOCK 4 — Modell-Routing, Chat-Proxy & PII

### Task 4.1 — LiteLLM & Provider-Anbindung
- [x] LiteLLM-Config: OpenAI (gpt-5.5, gpt-5.4-mini)
- [x] Anthropic (anthropic/claude-sonnet-4-6, anthropic/claude-haiku-4-5-20251001)
- [x] Google Vertex AI EU (vertex_ai/gemini-3.5-flash-tbd) — als Platzhalter mit enabled=false geseeded (live: enabled=f); echter Vertex-AI-EU-Zugang steht noch aus (Routing schließt ihn korrekt aus)
- [x] Mistral La Plateforme (mistral/mistral-large-latest, mistral/mistral-small-latest) — SOVEREIGN
- [x] models-Tabelle mit allen Modellen + (Recherche-)Preisen gefüllt via idempotentem Seed (`python -m app.seed_models`, ON CONFLICT DO UPDATE) — KEINE Preise in Alembic-Migration
- [x] Test-Skript: `scripts/test_providers.py` standalone (nicht in pytest, weil echte API-Calls)
- **DoD Phase A:** Code + Seed + Test-Skript gebaut, 28/28 Tests grün, Migration appliziert
- **DoD Phase B (User-Action nach Key-Eintrag, iterativ):** alle 6 aktiven Modelle antworten auf "Hello"; ggf. Model-IDs / Preise in `MODELS` korrigieren und re-seeden — ✅ de facto erfüllt: echte Anthropic- + Mistral-Antworten im Browser/Diag belegt (4.2), Keys in .env

### Task 4.2 — Chat-Frontend mit Konversations-Verwaltung ✅ (2026-06-04)
- [x] `/chat`-Route: Seitenleiste (Konversations-Liste + „Neue Unterhaltung") + Chat-Bereich (Verlauf, Eingabe, Modell-Dropdown)
- [x] Senden → Antwort anzeigen, non-streaming Lade-Zustand („denkt…" + Eingabe gesperrt, kein Doppel-Send)
- [x] Modell-Dropdown aus neuem `GET /models` (JWT, nur enabled, {name, provider})
- [x] Konversation wechseln lädt Verlauf; neue Konversation → `conversation_id`-Übernahme aus der Response (Folge-Nachrichten in dieselbe Konv.)
- [x] Fehler sichtbar (sonner-Toast + Inline), nicht still geschluckt
- [x] Client-Component reuse des `authHeaders()`-Musters aus /team (Browser-Supabase → getSession → Bearer)
- **DoD:** du kannst im Browser eine Anfrage abschicken statt nur mit curl zu testen ✅ (`npm run build` grün, 62 Backend-Tests grün; manueller Browser-Test durch User)
- Bewusst raus (geklärt): Verify-Badge (Option A), eigenes Branding/Fonts, Streaming (4.4), Löschen/Umbenennen, Teilen, Markdown-Rendering
- *Begründung: spart ab jetzt bei jedem weiteren Task Test-Zeit*

### Task 4.3 — Chat-Proxy-Endpoint ✅ (2026-06-04)
- [x] POST /v1/chat/completions (OpenAI-kompatibel, non-streaming) + persistente Konversationen (conversations/messages, RLS Workspace + privat-Filter)
- [x] Routing: model-Param → Provider via LiteLLM (configure_litellm im lifespan)
- [x] Policy-Check: Modell existiert + enabled (echter Tier-Check später, Tier-Feld fehlt)
- [x] Cost-Tracking: echte Tokens aus litellm.usage × Preis aus models
- [x] Audit-Event pro Call SYNCHRON in die Hash-Chain (LLM vor der Transaktion) — erster echter audit_events-Producer
- [x] Multi-Turn (conversation_id), Konversations-Endpoints (Liste/laden, privat)
- **DoD:** Chat-Request läuft, wird geloggt + auditiert + verifizierbar, Kosten erfasst ✅ (60 Tests grün, LLM gestubbt)
- Hinweis: echte LLM-Antworten = 4.1 Phase B (Keys), Frontend = 4.2.

### Task 4.4 — Streaming ✅ (2026-06-11)
- [x] Server-Sent-Events / Streaming-Response vom Provider durchreichen (StreamingResponse + litellm stream=True, OpenAI-SSE `data: {chunk}` … `[DONE]`)
- [x] Stream-Chunks an Frontend weitergeben (flüssige Anzeige) (api.ts `streamMessage` getReader/TextDecoder, chat-client live wachsende Blase)
- [x] Audit-Logging NACH Stream-Ende (vollständige Antwort sammeln) (`stream_chunk_builder` rekonstruiert ModelResponse+usage → 4.3-Write-Block wiederverwendet; Compliance-Garantie geerbt)
- [ ] PII-Hinweis vor Stream-Start (siehe 4.5) berücksichtigen → bewusst nach 4.5 verschoben (PII existiert noch nicht)
- [x] Test: langer Output streamt flüssig, wird vollständig geloggt (Kette unter Streaming verifiziert; + Abbruch-Fälle: Provider-Abort persistiert Teilantwort, Null-Content persistiert nichts)
- **DoD:** Streaming funktioniert end-to-end inkl. korrektem Audit ✅ (70 Tests grün, `npm run build` grün; manueller Browser-Test durch User)
- Test-injizierbarer Write-Transaction-Opener (`get_write_txn`-Factory) statt yield-Dependency, damit die kurze Audit-Transaktion NACH dem Stream öffnet (Advisory-Lock ms-kurz). non-streaming-Pfad (4.3) verhaltensgleich.
- Bewusste Lücke (dokumentiert): Teil- vs Vollantwort in der Kette nicht unterscheidbar (kein Marker-Feld); `completed`-Flag = Kandidat für spätere Audit-Härtung, neben `resp.model`-Mitloggen.

### Task 4.5 — PII-Detection & Sovereign-Routing
> In a/b gesplittet: 4.5a = Erkennung + Sovereign-Modus (fertig). 4.5b =
> Strict-Modus (anonymisieren → Cloud → de-anonymisieren), später.

#### Task 4.5a — PII-Erkennung + Sovereign-Routing ✅ (2026-06-11)
- [x] Microsoft Presidio als Docker-Service (eigenes Image mit deutschem spaCy-Modell `de_core_news_md`, `docker/presidio/`, Port 5002→3000)
- [x] Backend-Anbindung an Presidio (`app/pii.py`, httpx → `/analyze`, fail-safe bei Ausfall)
- [x] Erkennung: Email, Person, Telefon, IBAN (Presidio, sprach-agnostische Regex + de-NER), Steuer-ID (lokal, Mod-11-Prüfziffer)
- [x] Custom-Recognizer: deutsche Kundennummern (lokal, keyword-verankerte Regex)
- [x] Modus Sovereign: bei PII automatisch auf `sovereign_eligible`-Modell (Mistral) umleiten, egal welches Modell gewählt; gewähltes Modell schon sovereign → kein Reroute; kein Ziel enabled → 503
- [x] Workspace-Setting `pii_mode` in `workspaces.settings` (jsonb), Lese-Validierung → Default `sovereign`
- [x] Status im Response/Stream: `pii_detected, pii_check, routed_to, effective_model, anonymized(=false)` (Stream: erstes Event)
- [x] Frontend: dezente Notiz bei Reroute / Degraded
- [x] Tests: Reroute (stream+non-stream), Invariante 1 (Historie-Ratsche), Invariante 2 (Presidio-Ausfall fail-safe), Invariante 3 (Compliance/Kette), 503, schon-sovereign, Strict=Sovereign in 4.5a, Cost effektiv, lokale Regex
- **DoD:** sensibler Prompt nie ungeschützt an Nicht-Sovereign-Modell ✅ (84 Tests grün, `npm build` grün; Live-Smoke durch User)
- **Audit:** `request_text` bleibt Original; `pii_detected=true` nur bei echter Erkennung (Degraded-Reroute → false + `pii_check=unavailable`, via Log/Response sichtbar). `model`/`routed_to`/`cost` = EFFEKTIVES Modell.
- Bewusste Lücken (Security-Block): Erkennung statistisch (Falsch-Negative real); DSGVO-Spannungsfeld (PII in unlöschbarer Kette); Degraded-Reroute im Audit nicht vom Clean-Fall unterscheidbar; `de`-only-Scan.

#### Task 4.5b — Strict-Modus ✅ (2026-06-12)
- [x] PII anonymisieren (selbst über Presidio-Analyzer-Spans + lokale Regex-Spans, ein Mapping) → Cloud-Modell sieht nur Platzhalter → De-Anonymisierung der Antwort (auch im Stream, Boundary-Buffering)
- [x] Modus **pro Chat** wählbar: `conversations.pii_mode` (nullable, null=Workspace-Default), Request-Override persistiert auf die Konversation; Frontend-Umschalter neben dem Modell-Dropdown
- [x] `anonymized`-Flag echt gesetzt (true nur wenn Strict tatsächlich anonymisiert hat); `pii_detected` bleibt true auch im Strict-Fall (erkannt, nur anders behandelt)
- [x] **Modus in der unveränderlichen Kette getrackt**: Canonical **v2** (per-row Diskriminator) bindet `pii_mode` + `anonymized` in den Hash; Alt-v1-Ketten brechen nicht (DEFAULT-'v1'-Backfill), gemischte v1/v2-Kette verifiziert
- [x] I5: Audit speichert, was WIRKLICH an die Cloud ging — Sovereign=Original (real an EU), Strict=Platzhalter (nur die gingen raus). Löst das DSGVO-Spannungsfeld für Strict
- [x] I2-Fail-safe: Strict + Presidio-Ausfall → Reroute zu Sovereign (nie un-maskierte PII), transparente Notiz
- [x] Platzhalter-Format `[[DCY_<TYPE>_<n>]]` opak + toleranter Reverse (Case/Space/Markdown); unmatched bleibt sichtbar (kein PII-Leak)
- **DoD:** Strict anonymisiert vor dem Cloud-Call, de-anonymisiert korrekt (Stream+non-Stream), Modus pro Chat + in der Kette, alle Tests grün ✅ (**127 Tests grün**, `npm run build` grün; Live-Smoke: echter Strict-Prompt → nur Platzhalter an Anthropic, Presidio-Beweis)
- **KEIN** Admin-UI (Entscheidung: Wahl pro Chat im Frontend-Umschalter, kein Workspace-Setting-UI nötig)
- Bewusste Lücke: Strict = schwächere Compliance (Kontext geht trotzdem an die Cloud, nur PII anonymisiert) — im Frontend dokumentiert; PII-Erkennung statistisch (Presidio over-/under-detection, z.B. Verb „entwirf" als PERSON — über-maskiert ist safe)

### Task 4.6 — Fehlerbehandlung & Fallback ✅ (2026-06-12)
- [x] Provider-Timeout/Fehler abfangen, sauberer Error ans Frontend (non-stream 503/400/502 mit fixen Texten ohne Key-Leak; stream → sse_error)
- [x] Rate-Limit-Handling (Retry mit Backoff) (litellm `num_retries`+`timeout` per Modell, danach cross-model Fallback)
- [x] Fallback-Modell wenn Primär-Provider down (konfigurierbar) (`fallback_models`, Default = Sovereign-Modelle) — **sovereignty-aware**: PII-souveräne Anfrage fällt NUR auf `sovereign_eligible` zurück, nie Nicht-EU; kein Ziel → 503
- [x] Fehler werden geloggt (nicht im Audit, separates Error-Log) (`decyra.errors`-Logger; Hash-Kette bleibt rein; fehlgeschlagener Call = kein Audit-Event)
- **DoD:** Provider-Ausfall führt zu klarer Meldung, nicht zu Absturz ✅ (95 Tests grün, npm build grün; Invarianten 1–4 getestet)
- Streaming-Fallback nur vor dem ersten Chunk (danach festgelegt, Mid-Stream = 4.4); Status/Audit/Cost tragen das WIRKLICH genutzte Modell.
- **Betriebs-Hinweis:** Sovereign-Resilienz erfordert **≥2 enabled `sovereign_eligible` Modelle** (sonst harte 503-Kante bei Sovereign-Ausfall — korrekt, aber bekannt halten).

---

## TASK-BLOCK 5 — Wissensdatenbank (RAG)

### Task 5.1 — Dokument-Upload & Text-Extraktion
- [ ] POST /documents (Multipart-Upload)
- [ ] Formate: PDF (pdfplumber), Word (python-docx), TXT
- [ ] Datei-Validierung (Größe, Typ)
- [ ] Metadaten in documents-Tabelle, Datei in Object-Storage/lokal
- [ ] Endpoint: Dokumente des Workspace auflisten, löschen
- **DoD:** PDF/Word/TXT hochladen, Text extrahiert, gelistet

### Task 5.2 — Chunking & Embeddings
- [ ] Chunking: ~500 Tokens mit ~50 Token Overlap
- [ ] mistral-embed aufrufen, 1024-dim Vektor je Chunk
- [ ] Chunks + Embeddings + workspace_id in document_chunks speichern
- [ ] Batch-Verarbeitung (mehrere Chunks pro API-Call)
- **DoD:** hochgeladenes Dokument ist vollständig in document_chunks embedded

### Task 5.3 — Retrieval & Antwort
- [ ] Vektor-Suche: Query embedden, Top-k Chunks via Cosine (pgvector)
- [ ] Nur Chunks aus eigenem Workspace (RLS + WHERE)
- [ ] Chunks als Kontext in den Prompt einbauen
- [ ] Quellenangabe (Dokumentname + Chunk) in Antwort
- [ ] PII-Check auch auf RAG-Kontext → ggf. Sovereign
- [ ] Test: Frage zu hochgeladenem Dokument wird korrekt + mit Quelle beantwortet
- **DoD:** RAG-Antwort korrekt, mit Quelle, PII-sicher

---

## TASK-BLOCK 5B — Erweiterte Chat-Features

> Wie Langdock im Chat-Interface (Screenshot-Referenz), außer Agents/Workflows/Sprache (alle Phase 1.5).

### Task 5B.1 — Datei-Upload in Chat (Einzelanalyse)
- [ ] Datei an einzelne Chat-Nachricht anhängen (PDF, docx, txt, xlsx, csv)
- [ ] Unterschied zu RAG: Datei nur für diesen Chat-Kontext, nicht dauerhaft in Wissensdatenbank
- [ ] Text/Daten extrahieren, als Kontext an Modell
- [ ] PII-Check auch auf hochgeladene Datei
- **DoD:** Datei an Nachricht anhängen, KI antwortet darauf

### Task 5B.2 — Datenanalyse mit Chart-Generierung (Code-Interpreter)
- [ ] Sandbox für Code-Ausführung (isolierter Docker-Container, kein Netzwerk, Zeitlimit)
- [ ] Excel/CSV hochladen → KI schreibt Python (pandas) → in Sandbox ausführen
- [ ] Chart-Rendering (matplotlib) → Bild zurück in den Chat
- [ ] Sicherheit: Sandbox ohne Dateisystem-/Netzwerkzugriff, Ressourcen-Limit
- [ ] Fehler-Handling wenn Code fehlschlägt (Retry/Meldung)
- [ ] Test: Excel hochladen, "zeige Umsatz pro Quartal als Balkendiagramm" → korrektes Chart
- **DoD:** Datei → Analyse → Diagramm im Chat, Sandbox sicher isoliert
- *Achtung: aufwändigster Task im MVP, sicherheitskritisch. Eigene Session, ggf. mehrere.*

### Task 5B.3 — Vision (Bild-Upload)
- [ ] Bild an Nachricht anhängen (PNG, JPG)
- [ ] An vision-fähiges Modell durchreichen (GPT-5, Claude, Gemini, Pixtral für Sovereign)
- [ ] Modell-Auswahl beachtet Vision-Fähigkeit
- **DoD:** Bild hochladen, Frage dazu stellen, Antwort erhalten

### Task 5B.4 — Bildgenerierung
- [ ] EU-Provider klären: FLUX via Black Forest Labs (Freiburg, DE) — DPA prüfen!
- [ ] Alternativ Mistral Agents API Image-Connector
- [ ] Text → Bild Endpoint
- [ ] Bild im Chat anzeigen, Audit-Log-Eintrag
- [ ] Im Frontend: "Image"-Toggle wie bei Langdock
- **DoD:** Text-Prompt erzeugt Bild über EU-Provider, geloggt
- *Offene Klärung: Provider-Souveränität + DPA vor Bau bestätigen*

### Task 5B.5 — Prompt Library
- [ ] Tabelle prompts (id, workspace_id, user_id, title, content, shared bool)
- [ ] CRUD: Prompt speichern, bearbeiten, löschen
- [ ] Workspace-weit geteilte vs. persönliche Prompts
- [ ] Im Chat: Prompt aus Library einfügen
- **DoD:** Prompt speichern und im Chat wiederverwenden

### Task 5B.6 — Projects (Chat-Ordner)
- [ ] Tabelle projects (id, workspace_id, name)
- [ ] Chats einem Projekt zuordnen
- [ ] Seitenleiste: Chats nach Projekt gruppiert
- [ ] Chat-Verlauf mit Zeitgruppierung (Today, Last 7 days)
- **DoD:** Chats in Ordner gruppieren, Verlauf sauber sortiert

---

## TASK-BLOCK 6 — Frontend (Vollausbau)

### Task 6.1 — Chat-Interface vollständig
- [ ] Nachrichtenverlauf mit Verlauf-Speicherung
- [ ] Modell-Dropdown mit Tier-Beschränkung
- [ ] Streaming-Anzeige (aus 4.4)
- [ ] Status-Badge: Modell, umgeleitet/anonymisiert
- [ ] Quellenangaben bei RAG-Antworten (aufklappbar)
- [ ] Datei-Upload-Button im Chat (5B.1) + RAG-Wissensdatenbank-Toggle ("Company Knowledge")
- [ ] Chart-Anzeige im Chat (5B.2)
- [ ] Bild-Upload-Button (Vision, 5B.3)
- [ ] Image-Toggle für Bildgenerierung (5B.4)
- [ ] Prompt-Library-Zugriff im Eingabefeld (5B.5)
- [ ] Projekt-Zuordnung + gruppierte Seitenleiste (5B.6)
- **DoD:** voll nutzbarer Chat mit allen MVP-Features

### Task 6.2 — Admin-Dashboard: Übersicht & Logs
- [ ] Übersicht: aktive User, Anfragen heute, umgeleitet, blockiert
- [ ] Audit-Log-Liste mit Filter (User, Datum, Modell, Status)
- [ ] Detail-Ansicht je Event (Request, Response, Hashes)
- [ ] "Integrität prüfen"-Button → verify_chain
- [ ] CSV-Export der Logs
- **DoD:** Admin sieht und exportiert alle Logs, Verify funktioniert

### Task 6.3 — Admin-Dashboard: Verwaltung
- [ ] User-Verwaltung (einladen, Rolle ändern, deaktivieren)
- [ ] Einstellungen: erlaubte Modelle, Strict/Sovereign, PII-Stufe
- [ ] Dokument-Verwaltung (Liste, löschen)
- **DoD:** Admin kann Workspace vollständig konfigurieren

---

## TASK-BLOCK 7 — Browser-Extension

### Task 7.1 — Grundgerüst & Auth
- [ ] Manifest V3, Vite-Build, Icons
- [ ] Auth: Token aus Web-App-Session übernehmen (shared cookie/storage)
- [ ] Popup-UI mit Login-Status
- **DoD:** Extension lädt, ist gegen Backend authentifiziert

### Task 7.2 — ChatGPT.com-Integration
- [ ] Content-Script lädt auf chatgpt.com
- [ ] Eingabe/Senden abfangen
- [ ] Anfrage an Decyra-Backend statt OpenAI direkt
- [ ] Antwort im Seiten-Kontext anzeigen
- [ ] Sidebar: genutztes Modell, umgeleitet-Status, Link zum Audit-Log
- [ ] Test mit echtem ChatGPT-Account
- **DoD:** ChatGPT-Nutzung läuft durch Decyra, wird geloggt

---

## TASK-BLOCK 8 — Deployment & Pilot

### Task 8.1 — Production-Deployment
- [ ] Hetzner-Server provisionieren, Coolify installieren
- [ ] Docker-Compose: api, web, postgres(+pgvector), redis, presidio
- [ ] Domain + HTTPS (Let's Encrypt via Coolify)
- [ ] Production-Env-Variablen sicher setzen
- [ ] Postgres-Backup-Job (täglich, verschlüsselt, EU-Storage)
- [ ] Basis-Monitoring (Logs, Uptime-Check)
- [ ] Smoke-Test aller Kern-Funktionen in Production
- **DoD:** alles läuft stabil auf Production-Domain

### Task 8.2 — Pilot-Doku & Onboarding-Material
- [ ] Installationsanleitung Browser-Extension (für Mitarbeiter, 1 Seite + Screenshots)
- [ ] Kurz-Guide Web-App (Login, Chat, Dokumente)
- [ ] Admin-Guide für den IT-Leiter des Pilotkunden
- **DoD:** Material fertig, ein Laie kann der Anleitung folgen

### Task 8.3 — Pilot-Go-Live
- [ ] Pilot-Workspace anlegen, 10 User einladen
- [ ] Erste Firmendokumente hochladen
- [ ] Extension verteilen
- [ ] Vor-Ort-Schulung (1–2 Tage)
- [ ] Wöchentlichen Feedback-Termin + Feedback-Dokument einrichten
- **DoD:** Pilotkunde produktiv, Feedback-Loop läuft

---

## Regeln für Claude Code (jede Session)
- Plan Mode bei Tasks mit 3+ Schritten, erst nach Bestätigung bauen
- Pflicht-Tests nicht überspringen: Hash-Chain (3.2), PII-Routing (4.5), Multi-Tenant (2.4)
- Keine Secrets committen, kein Tech-Stack-Wechsel ohne Rückfrage
- Nach jedem Task: PROGRESS.md aktualisieren + Conventional Commit
- Ein Task pro Session; bei Architektur-Unsicherheit fragen statt raten
- Bei ~60% Kontext: dumpen → /clear → neue Session

---
*Reihenfolge bindend. Block 0 → 8. Innerhalb Block: Tasks der Reihe nach. Geschätzt ~20–24 Wochen bei ~15 h/Woche (erweiterter MVP mit allen Chat-Features außer Sprache).*

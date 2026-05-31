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
- [ ] OpenAI Business-Account + Zahlungsmethode + API-Key
- [ ] Anthropic API-Konto + API-Key
- [ ] Google Cloud + Vertex AI EU aktiviert + Service-Account-Key
- [ ] Mistral La Plateforme + API-Key
- [ ] Hetzner Cloud Account
- [ ] Supabase Projekt angelegt + Keys notiert
- [ ] DPA bei jedem Provider angefordert und abgelegt

### Task 0.2 — Lokale Umgebung
- [ ] Node.js 20, Python 3.11, Docker, Docker Compose installiert
- [ ] PostgreSQL 17 lokal oder via Docker lauffähig
- [ ] GitHub-Org `decyra` + SSH-Key hinterlegt

---

## TASK-BLOCK 1 — Projekt-Setup

### Task 1.1 — Repos & Grundgerüst
- [ ] decyra-api: FastAPI-Projekt-Struktur (app/, tests/, alembic/)
- [ ] requirements.txt: fastapi, uvicorn, litellm, sqlalchemy, alembic, psycopg2-binary, pydantic-settings, pytest, httpx
- [ ] GET /health Endpoint → {"status": "ok"}
- [ ] pydantic-settings für .env-Handling (alle Provider-Keys)
- [ ] Dockerfile für Backend
- [ ] decyra-web: Next.js 15 (App Router, TS), Tailwind 4, shadcn/ui init, Platzhalter-Startseite
- [ ] decyra-extension: Manifest V3 + Vite-Build-Skelett
- [ ] Pro Repo: README, .gitignore (Secrets/node_modules/__pycache__/.env), .env.example
- [ ] CLAUDE.md, WORKPLAN.md, PROGRESS.md ins decyra-api Root
- **DoD:** alle drei Projekte starten lokal fehlerfrei; /health antwortet

### Task 1.2 — Test-Infrastruktur
- [ ] pytest + pytest-asyncio konfiguriert in decyra-api
- [ ] conftest.py mit Test-DB-Fixture (separate Test-Datenbank)
- [ ] Erster Dummy-Test läuft grün
- [ ] (optional) GitHub Actions Workflow: pytest bei Push
- **DoD:** `pytest` läuft, mindestens 1 grüner Test

### Task 1.3 — Datenbank-Schema & Migrations
- [ ] Alembic initialisiert, verbunden mit lokaler DB
- [ ] pgvector Extension aktivieren (CREATE EXTENSION vector)
- [ ] Tabelle organizations (id uuid, name, created_at)
- [ ] Tabelle workspaces (id, organization_id FK, name, settings jsonb, created_at)
- [ ] Tabelle users (id, email unique, created_at)
- [ ] Tabelle workspace_members (workspace_id FK, user_id FK, role enum owner/admin/user, PK zusammengesetzt)
- [ ] Tabelle models (name PK, provider, cost_input numeric, cost_output numeric, eu_hosted bool, sovereign_eligible bool, tier_min)
- [ ] Tabelle audit_events (id, workspace_id FK, user_id FK, timestamp, model, request_text, response_text, pii_detected bool, routed_to, prev_hash, current_hash) — append-only
- [ ] Tabelle documents (id, workspace_id FK, filename, uploaded_by FK, created_at)
- [ ] Tabelle document_chunks (id, document_id FK, workspace_id FK, content text, embedding vector(1024), chunk_index)
- [ ] Row-Level Security auf allen Tabellen mit workspace_id
- [ ] Migration ausführen, Schema verifizieren
- **DoD:** Migration läuft sauber, alle Tabellen + RLS + pgvector vorhanden

---

## TASK-BLOCK 2 — Auth & Multi-Tenant

### Task 2.1 — Supabase Auth
- [ ] Supabase-Client im Backend + Frontend einrichten
- [ ] Email-Registrierung mit Bestätigungs-Mail
- [ ] Login (Magic Link bevorzugt, einfacher als Passwort)
- [ ] Auth-Middleware Backend: JWT validieren, user_id extrahieren
- [ ] Frontend: Session-Context, geschützte Routen, Logout
- **DoD:** User kann sich registrieren, einloggen, ausloggen; geschützte Route blockt ohne Login

### Task 2.2 — Workspace & Onboarding
- [ ] Beim ersten Login: Organization + Workspace automatisch anlegen, User wird Owner
- [ ] Endpoint: aktuellen Workspace + Rolle des Users zurückgeben
- [ ] Frontend: Workspace-Kontext global verfügbar
- **DoD:** neuer User landet in eigenem Workspace als Owner

### Task 2.3 — Einladungen & Rollen
- [ ] Einladungs-Token-Tabelle (token, workspace_id, email, role, expires_at)
- [ ] Endpoint: User einladen (Owner/Admin), Einladungs-Mail mit Token-Link
- [ ] Endpoint: Einladung annehmen (Token → workspace_member)
- [ ] Permission-Helper canManage(user, action) mit Rollen-Logik
- [ ] Endpoint: Rolle ändern, User deaktivieren (nur Owner/Admin)
- **DoD:** Einladung funktioniert end-to-end, Rollen werden erzwungen

### Task 2.4 — Multi-Tenant-Isolation (Test!)
- [ ] Alle Queries gehen über workspace_id-gefilterte Helper
- [ ] Test: User aus Workspace A kann Daten von Workspace B NICHT lesen
- [ ] Test: RLS greift auch bei direktem DB-Zugriff
- **DoD:** Isolations-Tests grün — kein Cross-Tenant-Zugriff möglich

---

## TASK-BLOCK 3 — Audit-Log mit Hash-Chain

### Task 3.1 — Hash-Chain-Mechanik
- [ ] Postgres-Trigger BEFORE INSERT auf audit_events
- [ ] current_hash = SHA256(prev_hash || workspace_id || user_id || timestamp || model || request || response)
- [ ] prev_hash = current_hash des letzten Events im selben Workspace (NULL beim ersten)
- [ ] Trigger/Permission: UPDATE und DELETE auf audit_events verbieten
- **DoD:** INSERT erzeugt korrekt verkettete Hashes; UPDATE/DELETE schlägt fehl

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
- [ ] Google Vertex AI EU (vertex_ai/gemini-3.5-flash-tbd) — als Platzhalter mit enabled=false geseeded, Vertex-AI-EU-Zugang steht noch aus
- [x] Mistral La Plateforme (mistral/mistral-large-latest, mistral/mistral-small-latest) — SOVEREIGN
- [x] models-Tabelle mit allen Modellen + (Recherche-)Preisen gefüllt via idempotentem Seed (`python -m app.seed_models`, ON CONFLICT DO UPDATE) — KEINE Preise in Alembic-Migration
- [x] Test-Skript: `scripts/test_providers.py` standalone (nicht in pytest, weil echte API-Calls)
- **DoD Phase A:** Code + Seed + Test-Skript gebaut, 28/28 Tests grün, Migration appliziert
- **DoD Phase B (User-Action nach Key-Eintrag, iterativ):** alle 6 aktiven Modelle antworten auf "Hello"; ggf. Model-IDs / Preise in `MODELS` korrigieren und re-seeden

### Task 4.2 — Minimales Test-Frontend (früh!)
- [ ] Einfache Chat-Seite: Texteingabe, Senden, Antwort anzeigen, Modell-Dropdown
- [ ] Ruft Backend-Chat-Endpoint auf (auch wenn noch nicht alles fertig)
- **DoD:** du kannst im Browser eine Anfrage abschicken statt nur mit curl zu testen
- *Begründung: spart ab jetzt bei jedem weiteren Task Test-Zeit*

### Task 4.3 — Chat-Proxy-Endpoint
- [ ] POST /v1/chat/completions (OpenAI-kompatibles Schema)
- [ ] Routing: model-Param → richtiger Provider via LiteLLM
- [ ] Workspace-Policy-Check: darf User dieses Modell im aktuellen Tier?
- [ ] Cost-Tracking: tatsächliche Input/Output-Tokens + Kosten loggen
- [ ] Audit-Event nach jedem Call schreiben (async, aus Block 3)
- **DoD:** Chat-Request läuft, wird geloggt, Kosten erfasst

### Task 4.4 — Streaming (eigener Task — kniffliger Teil)
- [ ] Server-Sent-Events / Streaming-Response vom Provider durchreichen
- [ ] Stream-Chunks an Frontend weitergeben (flüssige Anzeige)
- [ ] Audit-Logging NACH Stream-Ende (vollständige Antwort sammeln)
- [ ] PII-Hinweis vor Stream-Start (siehe 4.5) berücksichtigen
- [ ] Test: langer Output streamt flüssig, wird vollständig geloggt
- **DoD:** Streaming funktioniert end-to-end inkl. korrektem Audit

### Task 4.5 — PII-Detection & Sovereign-Routing
- [ ] Microsoft Presidio als Docker-Service starten
- [ ] Backend-Anbindung an Presidio
- [ ] Erkennung: Email, Person, Telefon, IBAN, Steuer-ID
- [ ] Custom-Recognizer: deutsche Kundennummern (Regex)
- [ ] Modus Strict: PII anonymisieren → Cloud-Modell → De-Anonymisierung der Antwort
- [ ] Modus Sovereign: bei PII automatisch auf Mistral umleiten
- [ ] Workspace-Setting steuert Strict vs. Sovereign
- [ ] Status im Response: pii_detected, routed_to, anonymized
- [ ] Test: Prompt mit IBAN wird korrekt umgeleitet/anonymisiert
- **DoD:** sensibler Prompt nie ungeschützt an Cloud-Modell

### Task 4.6 — Fehlerbehandlung & Fallback
- [ ] Provider-Timeout/Fehler abfangen, sauberer Error ans Frontend
- [ ] Rate-Limit-Handling (Retry mit Backoff)
- [ ] Fallback-Modell wenn Primär-Provider down (konfigurierbar)
- [ ] Fehler werden geloggt (nicht im Audit, separates Error-Log)
- **DoD:** Provider-Ausfall führt zu klarer Meldung, nicht zu Absturz

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

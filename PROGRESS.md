# PROGRESS.md — Decyra final

> Aktueller Stand. Claude Code aktualisiert das nach jeder Session. Vor jeder neuen Session zuerst lesen.

## Aktueller Task
**Block 4 — Routing/Chat/PII**: 4.4 (Streaming) abgeschlossen. Offen in Block 4: 4.5 (PII-Routing) und 4.6 (Fehler/Fallback). 4.1 Phase B de facto erledigt (echte Anthropic/Mistral-Antworten laufen).

## Status der Task-Blöcke
- [~] Block 0 — Voraussetzungen (0.2 lokale Umgebung erledigt: Node 20 via nvm, Python 3.11, Docker; 0.1 Accounts/Keys parallel)
- [x] Block 1 — Projekt-Setup ([x] 1.1 Repos, [x] 1.2 Tests, [x] 1.3 DB-Schema)
- [~] Block 2 — Auth & Multi-Tenant ([x] 2.1 Auth-Code + JWKS; [x] 2.2a Login-UI + Email/Passwort; [x] 2.2b Workspace/Org-Onboarding + Membership-Check; [x] 2.2c decyra_app-Switch + RLS scharf; [x] 2.3 Einladungen & Rollen; [ ] 2.4)
- [x] Block 3 — Audit-Log ([x] 3.1 Hash-Chain, [x] 3.2 Verify-Endpoints; async Write nach 4.3 verschoben)
- [~] Block 4 — Routing/Chat/PII ([x] 4.1 Phase A; [x] 4.2 Chat-Frontend; [x] 4.3 Chat-Proxy + Konversationen + Audit-Producer; [x] 4.4 Streaming; [ ] 4.5/4.6)
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

## Security-Härtung vor Pilot (Task 2.2)
Die drei 2.2-Punkte sind erledigt (siehe unten). NEU offen aus 2.3:

0. **Email-Verifikation für Einladungen.** Die 2.3-Einladungen sind
   EMAIL-gebunden: `onboard_user` matcht die Login-Email gegen
   `invitations.email` (nicht den Token). Damit ist eine Einladung nur
   so sicher wie die Email-Verifikation. Solange „Confirm email" im
   Supabase-Dashboard AUS ist (Dev), könnte sich jemand mit einer
   fremden eingeladenen Email registrieren und der Org beitreten.
   → **Vor Pilot: „Confirm email" im Supabase-Dashboard AN.** Kein
   Code-Blocker für 2.3, aber zwingend vor echten Usern.

Die drei ursprünglichen Punkte:

1. ~~**DB-Rolle decyra_app aktivieren.**~~ **Erledigt in 2.2c
   (2026-06-02).** App connectet jetzt als `decyra_app` (NOSUPERUSER,
   NOBYPASSRLS) via `DATABASE_URL`; Alembic + Seed via
   `MIGRATION_DATABASE_URL` (postgres). GRANTs + Rollen-Attribute +
   `onboard_user()` in Migration `d3f7a1c95b2e` (Single Source of
   Truth — conftest grantet NICHTS mehr). RLS feuert zur Laufzeit,
   live belegt: `pg_stat_activity` zeigt die laufende App als
   `decyra_app`, und `test_rls_blocks_cross_workspace_as_decyra_app`
   beweist Cross-Workspace-Isolation (B unsichtbar) mit
   Authentizitäts-Assertion (`is_superuser='off'`).

2. ~~**Membership-Check auf internem Verify-Endpoint.**~~ **Erledigt in
   2.2b (2026-06-01).** `verify_workspace_audit` prüft jetzt
   `SELECT 1 FROM workspace_members WHERE user_id=:u AND
   workspace_id=:w` → 403 wenn kein Member. TODO-Marker entfernt.
   Negativ-Test `test_internal_verify_non_member_returns_403` beweist
   den Check; die zwei internen Verify-Tests seeden jetzt eine
   Membership (sonst 403). `workspace_members` wird durch
   POST /onboarding gefüllt.

3. ~~**Login-UI reaktivieren + Email/Passwort.**~~ **Erledigt in 2.2a
   (2026-06-01).** Korrektur einer früheren Fehlannahme: Es gab NIE
   einen Login-Einstieg auf `src/app/page.tsx` und somit auch keinen
   „Conditional-Block aus git history" zum Cherry-picken — `git log -p`
   auf page.tsx zeigt nur das Entfernen der „Task 1.1"-Zeile, nie das
   Hinzufügen/Entfernen eines Auth-Links. In 2.2a wurde der Login-
   Einstieg FRISCH gebaut (Link `/` → `/login`) und `/login` von
   Magic-Link auf Email/Passwort umgestellt. Offen bleibt nur der
   bewusst verschobene Email-Bestätigungsflow (für Dev aus).

## Letzte Session
- 2026-06-11: Task 4.4 abgeschlossen. Streaming (SSE) mit erhaltener
  Compliance-Garantie (decyra-api + decyra-web).
  - **Kern-Hebel:** `litellm.stream_chunk_builder(chunks, messages=…)`
    (litellm 1.86.2, im Quelltext verifiziert: `calculate_usage` +
    `setattr(response,"usage",…)`) rekonstruiert aus den gesammelten
    Chunks dieselbe ModelResponse+usage wie der 4.3-Pfad → der komplette
    4.3-Write-Block wird wiederverwendet, die Garantie wird GEERBT, nicht
    nachgebaut.
  - **`app/chat.py`:** `rebuild_stream_response` (None bei leeren Chunks/
    leerem Content), `persist_stream_turn` (= 4.3-Write-Block, Quelle aus
    Chunks; legt Conversation erst hier an → kein Orphan bei Abbruch, weil
    decyra_app KEIN DELETE auf conversations/messages hat — live geprüft),
    SSE-Serializer `sse_chunk/sse_final/sse_error/sse_done` (OpenAI-Chunk-
    Form via reinen Attribut-Zugriff → echte litellm- UND Stub-Chunks).
  - **`app/main.py`:** `get_write_txn` = Factory-Dependency (kein yield →
    nichts wird über die Stream-Dauer gehalten; die Falle vermieden), in
    conftest auf die Per-Test-Connection überschreibbar. `chat_completions`
    verzweigt nach gemeinsamer Validierung: `stream=true` →
    `StreamingResponse(_stream_turn(...))`, sonst 4.3-Block VERBATIM in
    `with open_txn() as db_write:`. Chat-Endpoint nutzt `get_db_write`
    nicht mehr (onboarding unverändert). Der 4.3-Stream-Guard (400) ist
    durch den echten Pfad ersetzt.
  - **Abbruch (`_stream_turn`, drei Einstiege, kein yield im finally):**
    GeneratorExit (Client weg) → persist collected, re-raise; Exception
    (Provider-Abbruch) → persist collected + `error`-Event; else → persist
    + `sse_final(cid)` + `[DONE]`. CLIENT-Abbruch auditiert die GESAMMELTE
    Teilantwort (Gesammeltes ⊇ Gesehenes; drain-to-completion verworfen,
    Future-Hardening). PROVIDER-Abbruch: Teilantwort wurde gezeigt → wird
    auditiert. Null-Content (Fehler vor 1. Chunk) → nichts persistiert,
    kein Orphan (wie 4.3).
  - **conversation_id-Vehikel:** im finalen Chunk (nach Persist; für neue
    Konv. existiert die id erst dann — No-DELETE → keine Pre-Creation),
    bestehende Konv. zusätzlich im ersten Chunk geechot.
  - **Cost/Token:** aus `stream_chunk_builder`-usage → `compute_cost`
    unverändert; `stream_options={"include_usage":true}` als Best-Effort.
  - **Stub (`conftest.stub_llm`):** `completion(stream=True)` → Fake-Chunk-
    Generator (ein Chunk/Wort); `state["raise_after"]=k` → wirft nach k
    Chunks (k=0 = Null-Content). `stream_chunk_builder` gestubbt
    (rekonstruiert Content aus den ÜBERGEBENEN Chunks → Teil vs Voll
    unterscheidbar). Neuer `get_write_txn`-Override (beide app-Fixtures).
  - **Frontend:** `api.ts streamMessage` (getReader + TextDecoder, SSE
    `data:`-Parsing, `[DONE]`/`error`/`conversation_id`/delta). `chat-
    client send()` rendert live in eine wachsende assistant-Blase;
    „denkt…"-Skeleton nur bis zum 1. Token; Sperr-Zustand bleibt; Stream-
    Fehler via Toast+Inline. non-streaming `sendMessage` bleibt als
    Fallback exportiert.
  - DEBUG (Diagnose vor Fix): 1 roter Test war `test_chat_stream_rejected`
    (assertierte den 4.3-400-Guard) — durch 4.4 BEWUSST ersetzt, kein
    Regressions-Bug (69/70 grün, nur dieser Guard kippte). Test auf das
    neue Positiv-Verhalten (200 + text/event-stream) umgeschrieben
    (`test_chat_stream_now_supported`).
  - Verifikation: `pytest -q` **70 grün** (62→70: +8 Streaming-Tests in
    test_chat_stream.py, davon der Compliance-Test [Kette unter Streaming
    valid] der wichtigste; Guard-Test umfunktioniert statt neu).
    `npm run build` grün (10 Routen, /chat 43.7 kB). LLM+Stream gestubbt.
  - Bewusste Lücke (dokumentiert): Teil- vs Vollantwort in der Kette nicht
    unterscheidbar (kein Marker-Feld) — `completed`-Flag = Kandidat für
    spätere Audit-Härtung, neben `resp.model`-Mitloggen.
  - Scope: kein PII (4.5), kein Fallback/Router (4.6), kein neues Audit-
    Schema, non-streaming-Verhalten unverändert.

- 2026-06-04: Task 4.2 abgeschlossen. Chat-Frontend mit Konversations-
  Verwaltung (decyra-web) + EIN Backend-Touch (`GET /models`).
  - **Backend (decyra-api):** `GET /models` in `app/main.py` — JWT via
    `get_current_user`, nur `enabled=true`, Form `[{name, provider}]`.
    `models` ist RLS-frei (1.3) → KEIN `set_workspace_context`, KEINE
    Membership-Prüfung; jeder eingeloggte User darf die Liste sehen.
    `eu_hosted`/`sovereign_eligible` bewusst RAUS (späterer Einzeiler
    fürs Sovereign-Badge). `tests/test_models.py` (2 Tests): requires-
    auth (401) + only-enabled (disabled fällt raus, genau {name,
    provider}). **`pytest -q` 62 grün** (60→62).
  - **Frontend (decyra-web):** neue `/chat`-Route. Server-Shell
    `chat/page.tsx` (nur Auth-Guard wie /team) + Client-Insel
    `chat/chat-client.tsx` (interaktiv) + `chat/api.ts` (Fetch-Helfer).
    `authHeaders()` 1:1 aus team-actions.tsx übernommen (Browser-
    Supabase → getSession → Bearer) — NICHT neu erfunden.
  - Layout: Seitenleiste (Konv.-Liste + „Neue Unterhaltung", aktiver
    Eintrag via bg-sidebar-accent) links, Chat-Bereich rechts (Modell-
    Select im Header, scrollbarer Verlauf, Textarea unten). shadcn-
    sauber, Branding/Fonts bewusst generisch (Geist), nur Tokens.
  - `conversation_id`-Fluss: Send schickt NUR die neue user-Nachricht +
    `conversation_id ?? undefined` (Backend prependet die Historie
    selbst → keine Doppel-Historie). Bei neuer Konv. (activeConvId=null)
    kommt die ID aus der Response → wird übernommen, danach
    `GET /conversations` refresh → neue Konv. erscheint in der Liste.
  - Lade-Zustand (non-streaming, dauert Sek.): assistant-Platzhalter mit
    Loader + Skeleton „denkt…", Textarea + Senden-Button disabled, Enter-
    Submit blockiert → kein Doppel-Send. Enter sendet, Shift+Enter =
    Zeilenumbruch.
  - Fehler sichtbar: `api.ts` wirft mit `detail` aus 4.3 (oder Status);
    `chat-client` → sonner-Toast (destructive) + Inline-`role=alert`.
    Optimistische user-Nachricht bleibt bei Fehler stehen (Retry).
  - `selectedModel` beim Konv.-Wechsel/Neu BEWUSST nicht zurückgesetzt
    (bleibt User-Wahl; Modell-pro-Nachricht-Mischen ok, 4.3 speichert
    message.model). Kein undefinierter Zustand.
  - shadcn nachinstalliert: input, textarea, scroll-area, select, card,
    skeleton, sonner (zog next-themes + sonner als deps). `<Toaster />`
    in `layout.tsx`. „Chat"-Link im Dashboard neben „Team".
  - Verifikation: `npm run build` grün (TS strict, 10 Routen inkl.
    `/chat` 43.4 kB). Echte LLM-Antworten = 4.1 Phase B (Keys). Manueller
    Browser-Test (DoD) durch User.
  - Scope: kein Verify-Badge (Option A), kein Branding, kein Streaming
    (4.4), kein Löschen/Umbenennen/Teilen, kein Markdown-Rendering.

- 2026-06-04: Task 4.3 abgeschlossen. Chat-Proxy mit persistenten
  Konversationen — der ERSTE echte audit_events-Producer (Block 3 ⨯
  Block 4). Migration `c5d9e1f0a2b3` (down_revision b8e4f2a16c9d).
  - `conversations` (workspace_id FK, user_id FK, title, visibility
    DEFAULT 'private' CHECK('private'), created/updated_at) +
    `messages` (conversation_id FK CASCADE, workspace_id FK denorm.,
    role CHECK, content, model, prompt/completion_tokens, cost,
    created_at DEFAULT **clock_timestamp()** — sonst gleiche
    Turn-Timestamps → kaputte Reihenfolge, wie audit_events in 3.1).
    RLS = Workspace (FORCE) auf beiden; „privat" = expliziter
    user_id-Query-Filter (KEINE RLS-Härte → Privatsphäre-Test ist
    Wächter). GRANT conversations SELECT/INSERT/UPDATE, messages
    SELECT/INSERT (KEIN DELETE — kein Lösch-Feature in 4.3).
  - `app/chat.py`: compute_cost (Tokens/1M × Preis), derive_title
    (robust: erste user-Zeile, 60 Zeichen, Fallback), audit_request_text
    (alle NEUEN user-Msgs konkateniert, ohne Historie — keine
    forensische Lücke), build_openai_response, DB-Helper. messages.
    workspace_id IMMER aus der Konversation abgeleitet (nie aus Request).
  - `app/main.py`: POST /v1/chat/completions (OpenAI-kompatibel, non-
    streaming). Zwei Dependencies: db_read (Historie/Ownership) +
    db_write (Persist) — LLM-Call dazwischen, db_write idle (advisory
    lock erst am Audit-INSERT). conversation_id-Semantik: ohne →
    stateless, trotzdem Konv. angelegt+auditiert; mit → Historie laden
    + anhängen; beides → conversation_id gewinnt. **Compliance: KEIN
    Pfad ohne Persist+Audit.** stream:true → 400; Modell nicht
    enabled/unbekannt → 400; fremde/keine Konv. → 404; kein Member →
    403. GET /conversations + /conversations/{id} (privat, user_id-
    Filter, fremd → 404). lifespan ruft configure_litellm().
  - `tests/conftest.py`: autouse `stub_llm` (monkeypatcht
    litellm.completion → kein echter Call; .state/.calls zum Tunen/
    Prüfen).
  - `tests/test_chat.py` (11 Tests): OpenAI-Format, Persistenz, Multi-
    Turn (Stub sieht Historie+neu), Audit+Chain-verify (event_count
    wächst, valid), Cost (20.0 bei 1M/0.5M × 5/30), unknown/disabled
    Modell 400, stream 400, no-membership 403, get-with-messages,
    list-only-own, und PROMINENT die Privatsphäre als decyra_app
    (is_superuser='off'): User B sieht A's Konv. nicht (404 + leere
    Liste).
  - DEBUG (Diagnose vor Fix): 1 roter Test war ein TEST-Bug
    (`_seed_model` in test_list vergessen → 400 model not available →
    nichts angelegt), kein App-Bug — instrumentiert (POST-Status +
    Direkt-Count), Ursache gesehen, Model-Seed ergänzt.
  - Verifikation: `pytest -q` **60 grün** (49→60). Migration auf Dev
    appliziert, Grants verifiziert. Echte LLM-Calls bleiben 4.1 Phase B
    (User-Action mit Keys), in pytest gestubbt.
  - Scope: kein Streaming (4.4), kein Teilen (visibility immer private),
    kein PII (4.5), kein Router/Fallback (4.6), kein Tier-Check, kein
    Frontend (4.2).

- 2026-06-04: Task 2.3 abgeschlossen. Einladungen & Rollen — mehrere
  Mitarbeiter in einer Org. Migration `b8e4f2a16c9d` (down_revision
  d3f7a1c95b2e).
  - `invitations`-Tabelle (org-skopiert): id, organization_id FK,
    email, role workspace_role, token UNIQUE, invited_by FK, status
    (CHECK pending/accepted/expired/revoked), created_at, expires_at.
    RLS via NEUE GUC `app.current_organization_id` (org-Daten ↔ org-
    Kontext, getrennt von der ws-GUC). GRANT SELECT/INSERT/UPDATE
    (KEIN DELETE — Historie).
  - `onboard_user` ERWEITERT (Pfade 1+3 byte-identisch zu 2.2c): neuer
    Eingeladenen-Pfad (2) zwischen Idempotenz und Gründer — email-
    gebundener, pending, nicht-abgelaufener Invitation-Lookup → tritt
    bestehender Org bei (Rolle aus Einladung), markiert accepted,
    KEINE neue Org. SECURITY-DEFINER-Härtung beibehalten.
  - `current_user_membership(uuid)` SECURITY DEFINER (search_path
    gehärtet): löst user_id → (org, workspace, role) RLS-bypassed,
    liefert role für require_role. REVOKE PUBLIC + GRANT EXECUTE.
  - `app/invitations.py`: resolve_membership, require_role (403),
    create/list/revoke (explizite org-Filter als defense-in-depth
    ZUSÄTZLICH zur RLS), Token via secrets.token_urlsafe(32).
  - `app/main.py`: set_org_context (Bind-Param, nie f-string),
    POST/GET /invitations + POST /invitations/{token}/revoke, alle mit
    require_role({owner,admin}); owner-Einladung → 400 (nur admin/user).
  - `app/mail.py` (stdlib smtplib) + Mail-Config in config.py (Mailpit-
    Defaults). docker-compose: Mailpit-Service (1025 SMTP / 8025 UI).
  - Frontend decyra-web: `/team`-Seite (Server-Component listet
    Einladungen) + Client-Form (einladen) + Revoke-Buttons; Dashboard-
    Link auf /team. Funktional, nicht gestaltet.
  - DOKUMENTIERTE EIGENSCHAFTEN (bewusst, keine Lücken): (a) email-
    gebunden, nicht token-gebunden → braucht Email-Verifikation vor
    Pilot (Security-Block Punkt 0); (b) bestehender User mit Org wird
    eingeladen → Idempotenz-Pfad gewinnt, Einladung ignoriert (kein
    Multi-Org jetzt); (c) abgelaufene Einladung bleibt 'pending'
    (Zeitfilter expires_at>now()), Enum 'expired' bewusst ungenutzt,
    kein Cleanup-Job.
  - DEBUG (Diagnose vor Fix): erster pytest-Lauf 2 rote — `column
    "workspace_id"/"role" is ambiguous` im Eingeladenen-INSERT. Echte
    Reproduktion gegen die reale Funktion zeigte: Ursache war die
    `ON CONFLICT (workspace_id, user_id)`-Klausel — `workspace_id` ist
    OUT-Parameter der RETURNS-TABLE-Funktion und kollidiert unter
    `variable_conflict=error`. Der Gründer-INSERT hat kein ON CONFLICT
    → war nie betroffen. Fix: ON CONFLICT entfernt (unnötig — Pfad 1 +
    advisory lock garantieren null Memberships im Eingeladenen-Pfad).
  - Verifikation: `pytest -q` **49 grün** (35→49, +14 in
    test_invitations.py inkl. RLS-cross-org-Daten-Test als decyra_app).
    Mailpit-Stub in pytest (kein Socket). `npm run build` grün (8
    Routen inkl. /team). Live-Smoke: `app.mail.send_invitation_email`
    → Mailpit-API zeigt die Mail (To/Subject/Invite-Link korrekt).
  - Scope: kein Sichtbarkeits-/Teilen-System, keine Mehrfach-Workspaces
    pro Org, kein gestaltetes UI.

- 2026-06-02: Task 2.2c abgeschlossen. decyra_app-Rollen-Switch, RLS
  scharf geschaltet. Live gegen die laufende Dev-DB inspiziert (rolcanlogin
  war f → LOGIN fehlte; in Migration gefixt).
  - Migration `d3f7a1c95b2e`: Rolle härten (`ALTER ROLE decyra_app LOGIN
    NOSUPERUSER NOBYPASSRLS`, idempotenter DO-Block), `GRANT USAGE ON
    SCHEMA public`, per-Tabelle-GRANTs (audit_events nur SELECT+INSERT =
    append-only auf Rollen-Ebene; models nur SELECT; ws-skopierte
    SELECT/INSERT/UPDATE/DELETE; orgs/users SELECT/INSERT/UPDATE).
    KEINE Blankett-DEFAULT-PRIVILEGES (loud failing für künftige
    Tabellen — bewusste Entscheidung). `onboard_user(uuid,text)` als
    `SECURITY DEFINER SET search_path=pg_catalog,public` (Henne-Ei:
    user-Achsen-Idempotenz-Check inkompatibel mit ws-skopierter RLS →
    Funktion läuft als Owner, bypasst RLS für genau diese Logik;
    REVOKE PUBLIC + GRANT EXECUTE an decyra_app).
  - `app/onboarding.py`: `ensure_workspace` ist jetzt dünner Wrapper um
    `SELECT … FROM onboard_user(:u,:e)`.
  - `app/main.py`: `set_workspace_context` setzt
    `set_config('app.current_workspace_id', :ws, true)` mit Bind-Param
    (NIE f-String → keine Injection); verify + public_verify setzen den
    Kontext vor den Reads. get_db/get_db_write teilen weiter ein `_engine`.
  - `app/config.py`: `migration_database_url`. `alembic/env.py` +
    `seed_models.seed_default`: bevorzugen Migration-URL (Fallback
    database_url).
  - `tests/conftest.py`: GRANT-Block ENTFERNT (Migration ist Single
    Source of Truth; ein GRANT ALL hätte den append-only-Grant-Test
    wertlos gemacht). **`MIGRATION_DATABASE_URL` auf Test-DB gepinnt** —
    sonst leakt die .env-Dev-URL in die Tests und alembic upgrade liefe
    gegen die Dev-DB während decyra_test leer gedroppt bleibt (genau
    dieser Bug trat auf, diagnostiziert + gefixt). Neue Fixtures
    `app_with_db_decyra_app`/`client_decyra_app` (Override macht
    `SET LOCAL ROLE decyra_app` + Authentizitäts-Assertion).
  - `tests/test_rls.py` (neu, 3 Tests, alle als decyra_app):
    cross-workspace-isolation (B unsichtbar + WITH-CHECK-Block,
    Daten-Schicht), audit_events-append-only-grant (UPDATE → permission
    denied), endpoint-isolation (onboard→verify own 200 / foreign 403,
    voller Request-Pfad via client_decyra_app). Jeder asserted
    `current_user='decyra_app'` + `is_superuser='off'`.
  - Manipulations-Test (`test_internal_verify_tampered_row_is_detected`)
    bleibt BEWUSST postgres (modelliert privilegierten Angreifer;
    `session_replication_role` braucht Superuser; decyra_app kann gar
    nicht tampern). Dokumentiert, kein Trigger-Umbau.
  - `.env.example` + reale `.env`: DATABASE_URL=decyra_app,
    MIGRATION_DATABASE_URL=postgres. `docker/init-test-db.sql`: Dev-
    Wegwerf-Passwort `decyra_app_dev` (LOCAL ONLY, klar kommentiert).
  - Verifikation: `pytest -q` **35 grün** (32→35). Live-Smoke:
    App-URL connectet als decyra_app/is_superuser=off; uvicorn gestartet,
    `GET /v/{token}` → 200; `pg_stat_activity` zeigt die laufende App als
    `usename=decyra_app`. Dev-DB-Migration appliziert, decyra_app-Passwort
    gesetzt.
  - Scope: kein voller Multi-Tenant-Block (2.4), keine Einladungen (2.3).

- 2026-06-01: Task 2.2b abgeschlossen. Workspace/Org-Anlage beim ersten
  Login (decyra-api + decyra-web). Live-Schema vorab gegen die laufende
  DB per `\d` gegengeprüft — deckt sich mit der Migration.
  - `app/onboarding.py` (neu): `ensure_workspace(db, user_id, email)` →
    `OnboardingResult(workspace_id, workspace_name, created)`. Idempotent:
    advisory xact lock `pg_advisory_xact_lock(hashtext('onboarding:'||
    user_id))` (schützt den Idempotenz-Check vor Multi-Tab-Race), dann
    Membership-Query; existiert ein Workspace → zurückgeben, kein Insert.
    Sonst users→org→workspace→owner-membership in EINER Transaktion.
    `users.id = Supabase-sub` explizit (Mirror, weil
    `workspace_members.user_id` UND `audit_events.user_id` FK auf
    `users.id` sind). Auto-Namen: org=`f"{local}s Organisation"`,
    workspace=`"Standard-Workspace"`.
  - `app/main.py`: neue `get_db_write`-Dependency mit `engine.begin()`
    (Commit bei sauberem Exit, Rollback bei Exception) — getrennt vom
    read-only `get_db` (das weiter zurückrollt). Commit gehört in die
    Dependency, NICHT in den Endpoint (sonst zerschießt es die
    Test-Transaktion). Beide teilen dasselbe Modul-`_engine`. Neuer
    `POST /onboarding` (JWT, 400 wenn email-Claim fehlt). Membership-
    Check in `verify_workspace_audit` (403 für Nicht-Member), TODO weg.
  - `tests/conftest.py`: `app_with_db` überschreibt jetzt auch
    `get_db_write` auf die per-Test-Connection (engine.begin() läuft im
    Test nie; Fixture-Rollback hält Isolation).
  - `tests/_helpers.py`: `add_member(db, ws_id, user_id, role="owner")`.
  - `tests/test_verify.py`: zwei interne Tests
    (`…_intact_chain_returns_valid`, `…_tampered_row_is_detected`) seeden
    jetzt `add_member` (sonst 403 durch neuen Check); neuer
    `test_internal_verify_non_member_returns_403`. Public-Tests
    unverändert.
  - `tests/test_onboarding.py` (neu): requires-auth (401),
    creates-full-hierarchy (created=True, counts 1/1/1/1, role=owner,
    user_id=sub, email gespiegelt), is-idempotent (2. Call created=False,
    gleiche workspace_id, keine Duplikate).
  - decyra-web `src/app/dashboard/page.tsx`: `POST /onboarding`
    server-seitig vor dem `/me`-Call (gleicher Bearer-Token via
    getSession). Best-effort try/catch — schlägt es fehl, rendert das
    Dashboard trotzdem, nächster Load wiederholt. Workspace-Name/ID als
    sichtbarer Beweis angezeigt.
  - Verifikation: `pytest -q` 28→32 grün. `npm run build` grün (TS
    strict, 8 Routen). RLS kein Insert-Blocker, weil App als postgres-
    Superuser connectet (bestätigt; `SET LOCAL` erst mit 2.2c nötig).
  - Scope-Abgrenzung: kein Namens-Formular, kein decyra_app-Switch
    (2.2c), keine Multi-Workspace-/Einladungs-Logik, kein Chat.

- 2026-06-01: Task 2.2a abgeschlossen. Login-UI reaktiviert + auf
  Email/Passwort umgestellt (decyra-web). Reine Frontend-Arbeit, KEINE
  Backend-Änderung — `app/auth.py::get_current_user` validiert das
  Supabase-ES256-JWT generisch (JWKS, aud/iss/sub/email), unabhängig
  davon ob der User per Magic-Link oder Passwort eingeloggt ist. Per
  Inspektion + test_auth.py bestätigt.
  - `src/app/login/page.tsx`: Magic-Link (`signInWithOtp`) ersetzt durch
    eine Seite mit Toggle Login/Registrieren, ein Formular (Email +
    Passwort), zwei Submit-Pfade: `signInWithPassword` bzw. `signUp`.
    Bei Erfolg `router.push("/dashboard")` + `refresh()`; @supabase/ssr
    hat die httpOnly-Cookies da schon gesetzt. Supabase-Fehler (zu
    kurzes Passwort, „User already registered", falsche Credentials)
    landen sichtbar im UI (`role="alert"`). Guard: fehlt nach Erfolg
    `data.session` (= Confirm-email wäre an), klare Meldung statt
    stiller Redirect auf geschützte Route. KEINE eigene Passwort-Policy
    (Supabase-seitige Ablehnung reicht für 2.2a).
  - `src/app/page.tsx`: Login-Einstieg FRISCH gebaut — `next/link` →
    `/login`. Bleibt Server-Component. (Kein cherry-pick: der alte
    „Block aus git history" existierte nie, siehe Korrektur unten.)
  - `/auth/callback`-PKCE-Route bewusst UNVERÄNDERT gelassen — bei
    Passwort-Login ungenutzt, schadet nicht, hält Magic-Link als
    spätere Option offen.
  - Session bleibt server-side via httpOnly-Cookies (@supabase/ssr) —
    bestehender Mechanismus, nichts Neues erfunden.
  - Supabase-Dashboard (User-Action, erledigt): Authentication →
    Email-Provider AN, „Confirm email" AUS. Ohne Bestätigung liefert
    `signUp` sofort eine aktive Session → Dev-Flow ohne Inbox.
  - Verifikation: `npm run build` grün (TS strict, 8 Routen). Backend
    `pytest -q` 28/28 grün (kein Backend-Diff, reiner Regressions-
    Check). Manueller Browser-Test (beide Pfade: neuer Account via
    signUp + ausloggen/neu-einloggen via signInWithPassword, /me-Call,
    Logout) durch den User.
  - Scope-Abgrenzung: KEIN Workspace/Org (2.2b), kein decyra_app-Switch
    (2.2c), kein Chat, kein Email-Bestätigungs-/Passwort-Reset-Flow.

- 2026-05-31: Task 4.1 Phase A abgeschlossen. LiteLLM-Provider-Plumbing
  + idempotenter models-Seed in decyra-api:
  - Neue Migration `ebdf5bb9e9da_add_models_enabled.py`: schmale
    `ALTER TABLE models ADD COLUMN enabled BOOLEAN NOT NULL DEFAULT true`.
    Brauchte das Flag, damit Google als deaktivierter Platzhalter in
    der Tabelle leben kann ohne im Routing zu landen.
  - `app/llm.py`: `configure_litellm()` pusht Provider-Keys aus
    Settings in `os.environ`, damit `litellm.completion()` sie via
    Standard-Lookup findet. Vertex/Google bewusst nicht konfiguriert.
  - `app/seed_models.py`: 7 MODELS (6 aktiv + Google-Platzhalter)
    als ModelSeed-Dataclasses. Idempotenter
    `INSERT … ON CONFLICT (name) DO UPDATE`-SQL. Zwei Entrypoints:
    `seed_with_connection(conn)` für die per-test Transaktion,
    `seed_default()` für CLI (`python -m app.seed_models`).
    `slots=True` initial drauf gehabt → `m.__dict__` failed →
    Decorator auf nur `frozen=True` reduziert und auf explizite
    benannte Dict-Params umgestellt (robust gegen Feld-Renamings).
  - `scripts/test_providers.py` (neu, **nicht in pytest**): liest
    enabled Modelle aus DB, ruft je einen "Hello"-Call via LiteLLM,
    loggt OK/FAIL pro Modell. Erst nach User-Key-Eintrag laufen.
  - `tests/test_seed_models.py`: 4 Struktur-Tests grün (Insert-Count,
    Idempotenz, Mistral sovereign+eu_hosted, Google enabled=false).
    Keine API-Calls in pytest.
  - `.env.example`: Inline-Kommentar bei `GOOGLE_API_KEY`, dass Vertex
    AI EU noch aussteht und der Seed-Eintrag enabled=false ist.
  - Verifikation: Migration appliziert (`alembic upgrade head` →
    ebdf5bb9e9da), `python -m app.seed_models` zweimal → 7 Zeilen,
    keine Duplikate. psql zeigt Google enabled=f, Mistral eu_hosted=t
    + sovereign_eligible=t. `pytest -v`: 28/28 grün (24 vorher + 4
    neu).
  - **Phase B steht aus**: User trägt `OPENAI_API_KEY`,
    `ANTHROPIC_API_KEY`, `MISTRAL_API_KEY` in `.env` ein, läuft
    `python scripts/test_providers.py`. Erwartung iterativ — erster
    Lauf kann FAILs zeigen, wenn Model-IDs / Preise pro Account
    abweichen (Mistral-Aliase, OpenAI-Versionierung, Anthropic-Datum-
    Suffix). User korrigiert `MODELS` in `app/seed_models.py`, re-runt
    `python -m app.seed_models` (ON CONFLICT-Update), läuft das
    Test-Skript erneut. Idempotenter Seed ist genau dafür gebaut.

- 2026-05-31: Task 3.2 abgeschlossen. Verify-Endpoints in decyra-api:
  - `app/audit.py`: neue Funktion `verify_workspace_chain(db,
    workspace_id)` liest Events aus DB in Chain-Order (timestamp ASC,
    id ASC) und ruft die bestehende `verify_chain`. Keine Änderung
    an verify_chain / canonical_string / compute_hash.
  - `app/verify_token.py` (neu): `issue_verify_token` /
    `decode_verify_token`. HS256-JWT mit eigenem
    `AUDIT_VERIFY_SECRET` (getrennt vom Supabase-Secret), Claims
    `sub=workspace_id, iss="decyra-audit", iat, exp`. Default-TTL 30d,
    konfigurierbar via `AUDIT_VERIFY_TOKEN_DEFAULT_TTL_SECONDS`.
    `decode_verify_token` validiert auch `UUID(sub)` → 401 statt 500
    bei nicht-UUID sub.
  - `app/main.py`:
    `GET /workspaces/{workspace_id}/audit/verify` — JWT-geschützt via
    bestehender `get_current_user`-Dependency. Membership-Check als
    TODO (siehe Security-Härtung).
    `GET /v/{token}` — public, kein Supabase-Auth. Token-only.
    `get_db()` Dependency mit Module-Singleton-Engine
    + per-request rollback (read-only Default).
  - `tests/_helpers.py` (neu): `seed_workspace` / `insert_event` /
    `select_chain` extrahiert aus test_hash_chain.py (DRY).
  - `tests/conftest.py`: `AUDIT_VERIFY_SECRET` env override,
    `make_verify_token`-Fixture (eigene Secret-Override-Variante für
    Bad-Sig-Test), neuer `app_with_db`-Fixture mit
    `dependency_overrides[get_db]`, `client` umgebaut auf
    `app_with_db`.
  - `tests/test_hash_chain.py`: Local-Helpers durch
    `from tests._helpers import …` ersetzt, sonst unverändert.
  - `tests/test_verify.py` (neu): 7 Tests grün — internal-requires-
    auth, internal-intact, internal-tampered (PFLICHT, via
    `SET session_replication_role = 'replica'` als SUPERUSER),
    public-valid, public-expired, public-bad-sig, public-non-uuid-sub.
  - `WORKPLAN.md`: 3.2 ergänzt um Note, dass async Audit-Write nach
    4.3 (Chat-Proxy-Endpoint) wandert — der erste echte Producer.
  - Vorheriger "OFFENER SICHERHEITSPUNKT"-Block in dedizierte Section
    `## Security-Härtung vor Pilot (Task 2.2)` konsolidiert: drei
    Punkte (decyra_app-Role, Membership-Check, Login-UI/Email).
  - `pytest -v`: 24/24 grün (6 auth + 6 schema + 1 health + 4 hash-
    chain + 7 verify). Die 6 Auth-Tests aus 2.1 nach client-Fixture-
    Umbau alle namentlich grün — keine Regression.

- 2026-05-31: Task 3.1 abgeschlossen. Hash-Chain-Mechanik in decyra-api:
  - Neue Migration `36cbe1faa786_audit_hash_chain.py`: BEFORE INSERT
    trigger `audit_events_hash_chain_insert` auf audit_events.
    SECURITY DEFINER + fixed `search_path = pg_catalog, public`. Liest
    letztes `current_hash` desselben workspace_id (ORDER BY
    timestamp DESC, id DESC LIMIT 1), setzt NEW.prev_hash, berechnet
    NEW.current_hash = `encode(sha256(canonical), 'hex')`.
  - Im selben Migration-Step:
    `ALTER TABLE audit_events ALTER COLUMN timestamp SET DEFAULT
    clock_timestamp()`. `now()`/`transaction_timestamp()` liefert
    Transaction-Start-Time → mehrere Events derselben Transaction
    bekämen identische Timestamps und die ORDER-BY-Determinismus für
    den Vorgänger-Lookup wäre kaputt. `clock_timestamp()` liefert
    Wall-Clock pro Statement, pro Event eindeutig. Bug zuerst durch
    drei-Inserts-im-Workspace-Test reproduziert, Diagnose isoliert
    Python-Mirror als korrekt, Symptom auf Failure 1 zurückgeführt.
  - Concurrency: `pg_advisory_xact_lock(hashtext('audit_chain:' ||
    workspace_id))` serialisiert parallele Inserts pro Workspace,
    transaction-scoped Auto-Release.
  - Kanonisierung v1: `v1|<prev_or_empty>|<ws>|<user>|<iso8601_utc_us>|
    n:model|n:request|n:response`, n = `octet_length()` = UTF-8-Bytes.
    Mirror in `app/audit.py` (canonical_string, compute_hash).
  - `app/audit.py`: AuditEventForHash dataclass, `verify_chain(events)`
    akzeptiert Dataclass oder dict-rows, liefert
    VerifyResult{valid, event_count, broken_at}. Genesis = prev_hash
    NULL.
  - `tests/test_hash_chain.py`: 4 Tests grün (first-genesis, chain-3
    sequential, independent-workspaces, manipulation-pflichttest).
    Pflicht-Test füttert verify_chain mit getampter Liste, erwartet
    broken_at == 2.
  - SHA-256 ist Postgres-builtin seit PG 11 (`sha256(bytea)` +
    `encode(.., 'hex')`); kein pgcrypto.
  - Append-only-Trigger aus 1.3 unverändert; alle 1.3-Tests grün.
  - Migration läuft sauber gegen decyra (alembic upgrade head) und
    decyra_test (conftest-rebuild). `pytest -v`: 17/17 grün.
  - (Hinweis: der ursprünglich hier festgehaltene Sicherheitspunkt
    zum decyra_app-Switch ist jetzt im Top-Block
    "Security-Härtung vor Pilot (Task 2.2)" konsolidiert.)

- 2026-05-30: Auth-Flow in UI ausgeblendet, Backend + Frontend-Routen
  bleiben aktiv. Magic-Link-zu-Email-Passwort-Umstellung verschoben
  (→ in 2.2a erledigt).
  - Konkret entfernt: `decyra-web/src/app/page.tsx` zurück auf statische
    Server-Component (kein `async`, kein `getUser()`, keine Supabase-
    Imports).
  - **Korrektur (2026-06-01):** Die ursprüngliche Notiz „Reaktivierung =
    Conditional-Block aus git-history holen" war falsch. `page.tsx`
    hatte nie einen Auth-Link; der 2.1-Commit entfernte nur die
    „Task 1.1"-Zeile. In 2.2a wurde der Login-Einstieg frisch gebaut.
  - Unverändert: /login, /auth/callback, /dashboard, Logout-Button,
    src/lib/supabase/*, src/middleware.ts, Backend komplett, alle
    Tests (13/13 grün).

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
Zwei parallele Spuren:

A) **Task 4.1 Phase B (User-Action, ohne Claude)** —
   `.env` mit echten Provider-Keys füllen (OPENAI_API_KEY,
   ANTHROPIC_API_KEY, MISTRAL_API_KEY), dann
   `python scripts/test_providers.py` laufen. Erste FAILs sind
   erwartet (Model-ID-Drift pro Account); korrigieren in
   `app/seed_models.py::MODELS`, re-seeden, neu testen, bis
   alle 6 aktiven Provider "OK" zurückgeben.

B) **Task 4.2 (Test-Frontend, Claude)** — minimale Chat-Seite im
   Frontend, ruft einen noch nicht existierenden Backend-Endpoint
   (kommt in 4.3) auf. Frühe UI-Verifikation statt nur curl.

Start in nächster Session:
"Lies WORKPLAN.md und PROGRESS.md. Wir machen Task 4.2. Geh in Plan Mode."

# CLAUDE.md — Decyra final

> Projekt-Kontext für Claude Code. Wird automatisch bei Session-Start geladen. Kurz halten.

## Was wir bauen
EU-souveräne KI-Plattform für regulierten Mittelstand. Mitarbeiter nutzen führende KI-Modelle (OpenAI, Anthropic, Google, Mistral) in einem Tool. Sensible Daten werden automatisch erkannt (PII) und auf EU-Modellen verarbeitet. Alles wird manipulationssicher protokolliert (Hash-Chain-Audit). DSGVO- und EU-AI-Act-konform.

## Repos
- `decyra-api` — FastAPI Backend (KI-Proxy, Routing, Audit, RAG)
- `decyra-web` — Next.js 15 Frontend (Chat, Admin-Dashboard)
- `decyra-extension` — Chrome Extension (Manifest V3)

## Tech-Stack (nicht abweichen ohne Rückfrage)
- Backend: Python 3.11, FastAPI, LiteLLM, SQLAlchemy, Alembic
- Frontend: Next.js 15 App Router, React 19, TypeScript strict, Tailwind 4, shadcn/ui
- DB: PostgreSQL 17, pgvector, Row-Level Security
- Auth: Supabase Auth, Multi-Tenant (Organization → Workspace → User)
- PII: Microsoft Presidio (Docker-Service)
- Hosting: Hetzner + Coolify, Docker Compose

## Kern-Regeln (immer einhalten)
- **NIEMALS Secrets committen.** Keys nur in .env, .env in .gitignore.
- **audit_events ist append-only.** Niemals UPDATE oder DELETE darauf zulassen.
- **Jede DB-Query hat WHERE auf workspace_id.** Multi-Tenant-Isolation ist Pflicht.
- **PII-Check läuft vor jedem externen LLM-Call.** Keine Ausnahme.
- **TypeScript strict, Python type hints.** Kein `any`, kein ungetypter Code.
- Reference, don't duplicate: verweise auf package.json/requirements.txt statt sie zu kopieren.

## Modelle im MVP
Cloud: GPT-5, GPT-5 Mini, Claude Sonnet 4.6, Claude Haiku 4.5, Gemini 3.5 Flash
Sovereign (EU): Mistral Large 3, Mistral Small

## Datenmodell (Kern-Tabellen)
organizations → workspaces → workspace_members (role: owner/admin/user)
users, audit_events (Hash-Chain), models, documents, document_chunks (pgvector)

## Arbeitsweise
- Plan Mode für jede Aufgabe mit 3+ Schritten.
- Eine Phase = eine abgeschlossene Aufgabe. Siehe WORKPLAN.md.
- Tests schreiben für: Hash-Chain, PII-Routing, Multi-Tenant-Isolation.
- Bei Unsicherheit über Architektur-Entscheidung: fragen, nicht raten.
- Kontext nie über 60% füllen. Bei langen Sessions: Fortschritt in PROGRESS.md dumpen, dann /clear.

## Chat-Features im MVP (zusätzlich)
Datei-Upload in Chat, Datenanalyse mit Chart-Generierung (Code-Interpreter, Sandbox), Vision (Bild-Upload), Bildgenerierung (FLUX/Black Forest Labs, deutsch — DPA klären), Prompt Library, Projects (Chat-Ordner).

## Was NICHT im MVP ist (nicht bauen)
Agents (Phase 1.5), Workflows (Phase 1.5), Spracheingabe/STT (Phase 1.5), OneDrive-Sync (Phase 1.5), Memory (Phase 1.5), Berechtigungs-Vererbung (Phase 1.5), DACH-Connectoren/Integrations (Phase 1.5), SSO, Stripe, PDF-Reports, eigene GPU, Edge-Server, weitere Browser außer Chrome.

## Aktueller Stand
Siehe PROGRESS.md für den aktuellen Phasen-Stand.

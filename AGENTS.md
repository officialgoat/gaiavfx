# GAIAVFX contributor guide

GAIAVFX is a local-first, chat-first agentic movie workspace that drives ComfyUI.
Gaia is its chat agent. Users direct work and approve consequential changes.
Prepare, Direct, Agent, Review, Sets/Stages, Recast, replacement and voice share one app.

## Repository map
- `backend/src/gaiavfx/`: Python API, application services, domains and integrations.
- `backend/migrations/`: schema migrations; `backend/tests/`: CPU application tests.
- `frontend/`: React and TypeScript renderer with generated API contracts.
- `desktop/`: Electron shell and local service lifecycle.
- `engine_workers/`: isolated adapters for separately installed inference engines.
- `comfy_nodes/gaiavfx/`: the single maintained ComfyUI node pack.
- `scripts/`: development, packaging, contracts, hygiene and agent evaluation.
- `tests/`: browser tests, node tests and portable fixtures.
- `assets/brand/`: approved GAIAVFX marks, icons and palette.

## Architecture
- Maintain a modular monolith, with explicit public module services.
- Library, Catalog, Generation and Editing own their persistence.
- Application services own use cases and transaction boundaries.
- Domain rules are plain Python, independent of HTTP, UI and inference runtimes.
- ComfyUI performs inference; the application prepares and records work.
- Keep model prompt syntax and graph construction inside `integrations/`.
- Python is authoritative for persisted rules and prompt compilation.
- Regenerate TypeScript contracts from Python; never edit generated contracts.
- Avoid microservices, a message bus, Redis/Celery and dynamic plugin loading.
- Preserve human decisions, immutable run evidence and reproducible graph snapshots.

## Development and verification
Use Python 3.12, uv and Node.js with npm. Keep the Python environment at repo root.
PowerShell: `$env:UV_PROJECT_ENVIRONMENT = "$PWD/.venv"`.
Install: `uv sync --project backend --python 3.12` and `npm ci`.
Media tools: `npm run setup:media`; ComfyUI and engines are configured separately.
Run the web workspace: `npm run dev`; desktop shell: `npm run desktop`.
Regenerate contracts: `npm run generate:contracts`.
Check contracts and renderer: `npm run check:contracts` and `npm run build`.
Python checks: `.venv/Scripts/ruff.exe check backend`.
Formatting: `.venv/Scripts/ruff.exe format --check backend`.
Tests: `.venv/Scripts/python.exe -m pytest backend/tests -q -p no:cacheprovider --basetemp C:/gvt/a`.
Use a short writable temporary path; on other platforms substitute a local temp directory.
Browser tests: `npx playwright install chromium`, then `npm run test:ui`.
Hygiene: `npm run check:hygiene`; portable Windows build: `npm run package:win`.
On Unix use `.venv/bin/python` and `.venv/bin/ruff` for environment executables.

## Hygiene and UI
Keep weights, generated media, credentials and personal paths out of git.
Store local runtime tools in `.runtime/`, temporary work in `.cache/`, and libraries outside git.
Keep fixture media small, deliberate and portable. Never copy installed engine caches.
Format only edited files and run checks appropriate to the behavior changed.
UI work uses the Impeccable skill and the design system in `DESIGN.md`.
Preserve accessibility, keyboard navigation, both themes and human review boundaries.

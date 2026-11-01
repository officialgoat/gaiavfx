# Architecture

GAIAVFX is a modular monolith: React/TypeScript renders a shared movie workspace, a Python API owns persisted rules and use cases, and ComfyUI executes image/video inference graphs. The Electron shell manages the local service lifecycle. Gaia is a local assistant using an OpenAI-compatible endpoint. Separate subprocess workers handle stage fitting and optional voice engines.

## Module map

Paths below are under [backend/src/gaiavfx](../backend/src/gaiavfx).

| Layer | Responsibility |
|---|---|
| `api/` | FastAPI routes, request/response contracts, authentication and validation. |
| `application/` | Cross-module use cases and transaction boundaries: preparation, direction, rendering, review, Recast, stages and voice. `harness/` owns Gaia's execution loop, context, tools and human plan commands. |
| `modules/` | Explicit public services and owned persistence for Library, Catalog, Generation and Editing, plus Agent, Direction and Sets. Domain rules stay independent of HTTP and inference runtimes. |
| `integrations/` | Model-specific typed inputs, prompt compilation, graph construction and versioned knowledge. |
| `infrastructure/` | SQLite/SQLAlchemy access, ComfyUI and assistant gateways, FFmpeg/media processing, worker processes, credentials and coordination. |
| `setup/` | Manifest-driven dependency installation, read-only doctor checks and explicit inference smoke tests. |
| `shared/` | Common errors, context and telemetry interfaces. |

[frontend/](../frontend) consumes generated Python API contracts; do not hand-edit generated TypeScript. [desktop/](../desktop) hosts the renderer and local service. [comfy_nodes/gaiavfx/](../comfy_nodes/gaiavfx) is the single maintained custom node pack (nine nodes); third-party packs are installed separately.

### Where a concept lives

Each of these concepts has one module, and its callers and tests go through that module's interface. The terms are in [GLOSSARY.md](../GLOSSARY.md).

| Concept | Module | What it owns |
|---|---|---|
| Place text | `modules/direction/place_text.py` | The empty-place rule for a place's and an angle's text, enforced by Direction's commands. |
| Movie structure | `modules/direction/structure.py` | What a movie's rows must hold: nothing hangs on a removed row, and a segment's frames and cut chain hold. Commands check the rows they would leave; Undo the rows it would put back. |
| Direction journal | `modules/direction/journal.py`, `DirectionService.authored` | The rows an authored intent changed, written as one Receipt; Undo restores them and refuses when a later edit changed them. |
| Recast rules | `modules/recast/` | Shot replacement facts and planning, crops, head and mouth passes, replacement windows, and the film analysis and dialogue attribution policies, as pure rules with their shipped policies; Direction's timebase and capabilities are the kernel both share. |
| Stale revision | `shared/errors.py` (`StaleRevision`) | A lost compare-and-set and the revision the record is at now, which the API returns and Gaia words once. |
| Direction reading | `application/direction_reading.py` | The one configured Direction service and its sheet and set-view readers, which depend on modules only. |
| Shot bindings | `DirectionService.shot_references`, `planning.Binding` | One shot's views, pins and chosen sheet, judged once for freshness and suitability; a binding words its own problem. |
| Shot references | `application/shot_references.py` | An opening's or a cut's plan from its bindings, and the pictures a render lowers. |
| Take basis | `application/take_basis.py` | What a take was rendered from, and why it is no longer current, for Program, Export and the Join. |
| Movie read | `application/movie_read.py` | One read of a movie for the workspace: plans, pins, lanes, selections, receipts and undo targets. |
| Command scope | `frontend/src/features/shell/command-registry.ts` | Each command factory says which scope its commands act in; the registry composes the open document's commands, waits movie commands in a film or stage, and reports shared ids and shortcuts. |
| Attempts and outcome | `modules/generation/operations/attempts.py`, `outcome.py` | A producer's tries at one product, and why an operation stopped, as a kind. |
| Operation kinds | `application/operation_kinds.py` | Per kind: what it prepares, how its candidates' review opens, whether that review still waits on the person, and whether it is a Recast render. Gaia, the routes and the renderer read it. |
| Stage build | `application/set_build.py`, `sets.build_view` | A stage build's operation and its readings: state, stop cause, spend, and the workbench's label and banner. |
| Addressed issues | `modules/sets/service.py` (`addressed_issue_ids`) | The issues a built version addressed, for its review and for settling them. |
| Stage run plan | `application/sets.py` (`run_plan`, `enqueue_fix`) | What Run queued fixes runs on a version, and the one admission for a fix. |
| Character derivatives | `application/recast_derivatives.py` | A character's face sheet and voice model: feed, approval, cancel and reconcile. |
| Run scope | `application/recast_run.py` | Which shots a Recast run renders, its reservation and its estimate, and why Plan, Start and Pause may not run (`refusals`, `start_blocked`). |
| Shot work | `application/recast_take.py` | The seam through which Recast asks for analyses, replacements, bridges and assembly. |
| Conversation record | `application/harness/conversation.py` | A session's turns, its saved document and its summary. |
| Grant budget | `application/harness/budget.py` | What Gaia has reserved, the session's limit, the current step's allowance, and every "jobs left". |
| Report checks | `application/harness/tools/report_checks.py` | The current human turn and what a check remembers in it, read from the turns; say and ask_human run through it. |
| Turn context | `application/harness/turn_context.py` | What Gaia sees in a call, assembled once, with the narrower readings plan review and the view use. |
| Turn request | `application/harness/request.py` | The tools offered, their schema and the assembled request, and the reply parsed against the offer. |
| Plan proposal checker | `application/harness/tools/plan_check.py` | Completing and checking a proposed plan. |
| Plan review | `application/harness/session.py` (`ReviewRef`, `Plan.current`) | What the user reviews to finish a step, and the step a plan is on. |
| Tool admission | each tool's declaration (`application/harness/actions.py` `Tool`) | Which step kind, scope and overflow rank admit a tool; the registry derives its tables. |
| Process files | `application/process_media.py` | Which media are process files the Library hides. |
| Typed routes | `frontend/src/shared/routes.ts` | Requests typed from the generated contracts. |
| Navigator | `frontend/src/app/navigator.ts` | The shell's workspace, Library section, open document and open stage. |

## From conversation to a take

```mermaid
flowchart LR
    Chat[Chat with Gaia] --> Plan[Proposed plan]
    Plan --> Approval[Human approval]
    Approval --> Jobs[Application use cases and durable jobs]
    Jobs --> Graphs[Typed graph builders]
    Graphs --> Gateway[ComfyGateway]
    Gateway --> Comfy[Local ComfyUI]
    Comfy --> Collect[Collect outputs into Library]
    Collect --> Tracks[Candidates on shared tracks]
    Tracks --> Review[Human review and final takes]
```

[harness/plans.py](../backend/src/gaiavfx/application/harness/plans.py) applies human approval with a session revision check. The grant freezes job budget and permitted step kinds; tools cannot invoke human approval commands. Film conversations plan before rendering. Execution can pause for review and resume after a human decision.

Application use cases prepare references and compile graphs through integrations. Graph construction happens before submission: [Generation's job records](../backend/src/gaiavfx/modules/generation/operations/persistence.py) persist the graph, inputs, seed and knowledge snapshot alongside operation and candidate evidence. The diagram shows the logical dependency path; graphs are already frozen when the durable jobs execute.

[application/operations.py](../backend/src/gaiavfx/application/operations.py) claims jobs, stages inputs, validates bindings, submits and polls, then collects outputs. [ComfyGateway](../backend/src/gaiavfx/infrastructure/comfy.py) validates graph classes and numeric inputs against `/object_info`, uploads inputs and downloads outputs through ComfyUI's API. Jobs retain endpoint, remote identifier and output descriptors for recovery. Transactions are short; no transaction spans a model call or a durable wait.

Collection registers media/assets or managed artifacts through Library and links candidates to their operations. Direction and Editing project results into the same tracks used by the manual UI. Blind review rankings and selected takes are separate human decisions; an agent plan does not replace final take selection.

## Integrations and knowledge

| Integration | Knowledge and execution |
|---|---|
| `minimax_h3/` | H3 rendering, continuation, replacement, survey and probes. `knowledge/` contains `pack`, `head`, `render`, `replacement`, `survey`, `probe` and `wording` TOMLs. `continuation.py` holds the handoff rule ([CONTINUATION.md](CONTINUATION.md)). |
| `qwen_image_edit/` | Qwen image-edit graphs and prompt wording, with `render.toml` and `wording.toml`. |
| `person_analysis/` | Face analysis and SAM tracking graphs; `knowledge/render.toml` declares model settings. |
| `worldbuilder/` | Geometry extraction, take-to-stage rules and fill planning; `take`, `rules`, `fill` and `engines` TOMLs. |
| `voice/` | Experimental voice specifications and expansion rules; `voice`, `expansion` and `engines` TOMLs. |

Knowledge TOMLs hold model-specific settings and rules near their compilers. Python is authoritative for prompt syntax and persisted behavior. Snapshots preserve the resolved knowledge used by a job; changing defaults does not silently rewrite its run evidence. Integrations are explicit imports, not dynamically loaded plugins.

## Isolated engines

[application/engine_jobs.py](../backend/src/gaiavfx/application/engine_jobs.py) builds `JobSpec` entries with `executor="engine"` and a registered `<engine>.<task>` step. Snapshots freeze registry fingerprints, worker hashes and GPU requirements. [engine_workers/](../engine_workers) implements the subprocess file protocol; [infrastructure/engines.py](../backend/src/gaiavfx/infrastructure/engines.py) supervises processes and cancellation (including Windows process trees).

Workers use their engine's interpreter and installed dependencies, with app Python environment variables stripped and model downloads disabled. GPU jobs share a lease with ComfyUI and request model unloading before running. CPU jobs need no GPU lease. There is no Redis/Celery queue or message bus.

Worldbuilder uses gsplat in `.runtime/engines/worldbuilder` by default. Its installer records `manifest.json` and `probe.json`; Settings/doctor use this evidence. Voice engines are separately installed and experimental. The assistant's llama.cpp server is managed independently of ComfyUI; both use local weights.

## Library layout

A library is a user-selected directory, separate from source code and installed weights. The web development launcher uses `GAIAVFX_LIBRARY`, falling back to `.cache/library`. [LibraryService](../backend/src/gaiavfx/modules/library/application.py) creates the database and initial media directories; additional folders appear as needed.

```text
library/
  library.sqlite3       # metadata, settings, sessions, jobs, graphs, reviews and edits
  originals/            # imported media
  generated/            # collected generated media
  continuations/        # paired H3 audio/video latent context, named by SHA-256
  artifacts/            # hashed geometry, masks, stage splats and other managed blobs
  exports/              # rendered exports
  cache/                # derived previews and job work files
  credentials/          # optional encrypted assistant credentials
  telemetry/            # local observation/delivery journal
```

Database migrations live in [backend/migrations](../backend/migrations). Module services own their rows; application services coordinate changes. Media and artifact records retain content hashes and provenance. Setup defaults in `.runtime/setup.json` seed new libraries; existing library settings remain their own decisions.

## Setup contract

[setup/manifest/comfy.toml](../backend/src/gaiavfx/setup/manifest/comfy.toml) is the single source of truth for supported ComfyUI versions, node-pack pins, model filenames/folders, sizes, hashes, licences and feature membership. [runtime.toml](../backend/src/gaiavfx/setup/manifest/runtime.toml) pins media, assistant and worldbuilder installation dependencies. The downloader streams to resumable `.part` files and verifies SHA-256 before replacing targets.

[test_every_graph_class_is_declared_for_its_feature](../backend/tests/test_setup_manifest.py) scans every graph builder's AST, including the allowed dynamic LoRA loaders, and verifies each emitted node class is declared for its feature. Manifest validation also checks model/feature membership in both directions. This catches dependency drift when builders change; it does not establish inference quality.

The CLI and Settings Setup check share doctor checks. Doctor queries ComfyUI node/model availability and reads local files/probe evidence without running inference. `smoke` deliberately runs the production graph builders with tiny fixtures and records `graph.json`, `report.json` and outputs. See [INSTALL.md](INSTALL.md) for commands and their side effects.

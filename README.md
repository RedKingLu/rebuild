# rebuild

> **Language / 语言**: English (this page, a translation) · [中文](./README.zh.md)
> The **Chinese version is authoritative** for this repository's public documentation. This English page is a translation of it; where the two disagree, **the Chinese version governs**.
> See [`.i18n.yaml`](./.i18n.yaml) for the language-variant manifest. Links on this page point to the English variants.

**A software rebuilding platform** — it uses goal-driven AI agent orchestration to turn "convert an existing piece of software into another form" into a traceable, verifiable, evidence-backed engineering process.

Current version `V26.2` · License [MIT](./LICENSE) · Language: English (this page, a translation) / [中文](./README.zh.md) (authoritative)

---

## What this is

rebuild takes in a body of **source code that actually exists**, and — around a **decidable target state** (change the tech stack, change the database, change the runtime environment, upgrade a framework, split modules, …) — drives a set of agents through the whole course from intake, fact-basing, assessment, planning, execution and verification to delivery, leaving reviewable evidence at every step.

What it is **not**:

- Not a chat-style coding assistant — it advances stage by stage per project, not answer by answer per conversational turn
- Not an unattended one-click migration black box — the critical junctures must be adjudicated by a human (see "user gates" below)
- Not a general-purpose agent marketplace or general AI office tool — tasks without source code and without machine-verifiable output are out of scope

## What it can do

### Typical scenarios (the platform's focus, with comparatively richer supporting resources)

| Scenario | Identifier | Description |
|---|---|---|
| Domestic-stack switchover | `xinchuang_switch` | Migration to domestic hardware, software and database stacks |
| Software modernization | `modernization` | Upgrading legacy tech stacks, modernizing architecture |
| Software porting | `porting` | Porting across platforms, runtimes and languages |

### Open extension

The scenario system is **open**. The typical scenarios above are the directions the platform invests in most heavily, and they are **not the whole set** — the platform supports scenarios "**including but not limited to**" these three, and lets you **modify existing scenario packs or add your own custom ones**. The intended shape for adding a scenario is "drop in one scenario-pack directory and it takes effect", not editing an enumeration in the code.

### The three admission criteria for a scenario

For any scenario (typical or custom) to enter the pipeline, all of the following must hold at once:

1. **There is source code** — real code that can be obtained and read
2. **There is a decidable target state** — you can state clearly "what counts as success"
3. **The output is machine-verifiable** — the result can be checked by compilation, tests, diff, or equivalence checking

If any one of the three is missing, the platform will tell you plainly that "this is not a good fit for rebuild", rather than pushing through a run anyway and handing you a report that merely looks presentable.

## Workflow

A project advances through seven trimmable stages, P0–P6:

```
P0 Intake  →  P1 Fact-base  →  P2 Assess  →  P3 Plan  →  P4 Execute  →  P5 Verify  →  P6 Deliver
```

- **P0 Intake**: import source code from a local directory / Git repository / ZIP archive / GitHub repository
- **P1 Fact-base**: establish the project's source of facts (structure, tech stack, dependencies, build and run procedures, configuration, risk leads)
- **P2 Assess**: assess migration and rebuilding risk along multiple dimensions; produce an assessment report, a risk list, and open decision items
- **P3 Plan**: form a plan and a task graph (TaskGraph)
- **P4 Execute**: carry out the real modifications node by node along the task graph, producing code and patches
- **P5 Verify**: deterministic verification — compilation / tests / diff / equivalence checking
- **P6 Deliver**: consolidate artifacts and evidence

The pipeline is trimmable — for an assessment only, for instance: `P0 → P1 → P2 → P6`.

## Core design commitments

These are what separate rebuild from an automation script built on "it seems to run, ship it":

**1. capability-first: when a capability falls short, report it honestly; never fake success**

The platform explicitly distinguishes "done" from "cannot do". When valid model credentials are missing, when there is no bindable source code, when the environment is not in place, or when evidence is insufficient, it returns **`blocked`** or **`evidence_gap`** — it will **not** degrade into placeholder content, mock data, or rule templates to pass itself off as finished. An honest conclusion saying "I did not manage this, because X" is preferable to a pretty artifact that cannot be traced.

**2. Critical junctures have user gates (human adjudication)**

Stage promotion and high-risk actions are gated: a human must review the evidence and authorize. This is a **design choice**, not automation that has yet to be finished — responsibility for modifying code should not rest with a model alone.

**3. No Evidence No Completed**

Every "completed" must have evidence hanging off it. Artifacts, verification records and execution traces (Trace) can be reviewed after the fact.

**4. Reasoning belongs to the LLM; collection and verification belong to deterministic code**

Recognition, comprehension, planning and risk judgement go to agent reasoning; cloning code, listing files, running commands, counting, compiling, running tests and producing diffs go to deterministic tools. Neither impersonates the other.

**5. BYOK (bring your own model credentials)**

You supply the model API keys yourself; they are read from environment variables only and stored encrypted, and the API returns only credential status, never echoing the plaintext.

## Current maturity (stated honestly)

**rebuild is currently at the stage of being runnable and able to complete an end-to-end run, but still under active construction. It is not a turnkey, fully automatic migration platform.**

Already usable:

- Backend and frontend start locally; projects can be created and advanced through P0–P6
- The trunk paths — stage orchestration, gates, evidence and Trace, task graph, model gateway, workspace isolation and the execution sandbox, code-hosting integration — are implemented and have self-verification evidence
- The backend test suite is complete and can be run in full

**Things to be mentally prepared for (these are the current state of affairs, not a disclaimer):**

| Item | Current state |
|---|---|
| **Human intervention is required** | Stage promotion requires user-gate authorization; modified artifacts require human review before adoption. There is no "submit a repository URL and come back to collect the goods" usage |
| **You must supply your own model credentials** | With no valid key, the affected capabilities honestly report `blocked`. The platform provides no built-in model, and it will not degrade into a rule engine to pretend it finished |
| **Machine verification ≠ a guarantee of business equivalence** | Compilation passing, tests passing and a readable diff do not amount to fully equivalent business semantics. Semantic equivalence still needs your domain knowledge to vouch for it |
| **Some capabilities are in an incomplete state** | A small number of capabilities are still marked partially implemented or planned; the interface presents them with explicit capability-status markers and does not show them as complete |
| **Open extension of scenario packs is still being built** | Support for the typical scenarios is comparatively mature; "adding a custom scenario pack with zero code changes" is the settled direction and is still being worked toward |
| **No multi-user or permission system** | Currently intended for **single-machine, single-user** local use: there is no login, no accounts, no tenant isolation. **Do not expose the service directly to the public internet** |
| **In-container execution is off by default** | Containerized execution requires an explicit opt-in and mounting the Docker socket (equivalent to handing over control of the host's Docker), and is recommended only in a trusted single-machine environment |
| **No prebuilt distributions** | No PyPI package, npm package or public image has been published yet; build locally from source as described below |

## Quick start

### Requirements

| Component | Requirement |
|---|---|
| Python | >= 3.12 |
| Package manager | [uv](https://github.com/astral-sh/uv) |
| Node.js | 20 LTS or above recommended |
| Database | SQLite by default, works out of the box (PostgreSQL optional) |

### Backend

```bash
cd backend
cp .env.example .env        # fill in per the comments; keys go only into the local .env, which is never committed
uv sync --dev
uv run uvicorn app.main:app # listens on 8000 by default
```

The first startup creates tables and writes seed data automatically — no manual database migration needed. If the actual database lags behind the migration revision, startup reports it explicitly at ERROR level instead of handling it silently.

Health check:

```bash
curl http://localhost:8000/api/health
```

> All endpoints are mounted under the `/api` prefix; the health check is `/api/health` (not `/health`).

### Frontend

```bash
cd frontend
npm install
npm run dev                 # dev server on 5173
```

Open http://localhost:5173 .

### Container mode (optional)

```bash
cp .env.example .env        # used by docker compose's env_file
docker compose up --build
```

Frontend at http://localhost:8080 , backend at http://localhost:8000/api/health .

### Port convention

| Service | Port |
|---|---|
| Backend | **8000** (same for development and containers) |
| Frontend (development) | **5173** |
| Frontend (container) | **8080** |

Please do not introduce other ports — ports are kept to a single convention across code, configuration, scripts and container orchestration.

## Configuration

Two example files list every configurable item, **containing variable names and explanations only, never any real values**:

| File | Purpose |
|---|---|
| `.env.example` → `.env` | Injected via `docker compose`'s `env_file` (containerized deployment) |
| `backend/.env.example` → `backend/.env` | Local development (read by pydantic-settings, prefix `REBUILD_`) |

Key points:

- **`REBUILD_MASTER_KEY`**: the master key for credential encryption. It must be set to use the credential (BYOK) feature. To generate one:
  ```bash
  python -c "import secrets; print(secrets.token_hex(32))"
  ```
- Model keys go through `{PROVIDER}_API_KEY` or the generic `LLM_API_KEY`, and belong **only in the local `.env`**. `.env` is already protected by `.gitignore`.
- Never write any key / token / secret into code, configuration files, logs, issues or commit records.

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12 · FastAPI · uv · SQLAlchemy 2.0 · Alembic |
| Agent orchestration | **LangGraph** (the single orchestration substrate, with checkpoint / interrupt / resume) |
| Model access | A unified ModelGateway → LiteLLM adapter layer (no direct provider connections, no hard-coded model names or endpoints) |
| Frontend | React · Vite · TypeScript |
| Storage | SQLite (default) / PostgreSQL (optional) |
| Deployment | Docker Compose (including the controlled execution-sandbox image) |

## Code layout

```
backend/     FastAPI service, agent orchestration, model gateway, data models and migrations
frontend/    React + Vite frontend
source/      platform runtime resources (skills / cases / resource manifests)
deploy/      deployment and execution-sandbox build contexts
scripts/     operations and maintenance scripts
```

## Contributing

Contributions are welcome. Please read first:

- [CONTRIBUTING.md](./CONTRIBUTING.md) — development environment setup, testing requirements, commit conventions
- [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md) — community code of conduct
- [SECURITY.md](./SECURITY.md) — **report security vulnerabilities through the private channel described in that file; do not open a public issue**

Before submitting, please confirm: no hard-coded secrets, backend tests passing, frontend builds.

## License

[MIT](./LICENSE)

# ARCHITECTURE — How rebuild is implemented

> **Language / 语言**: English (this page, a translation) · [中文](./ARCHITECTURE.zh.md)
> The **Chinese version is authoritative** for this repository's public documentation. This English page is a translation of it; where the two disagree, **the Chinese version governs**.
> See [`.i18n.yaml`](./.i18n.yaml) for the language-variant manifest. Links on this page point to the English variants.

> Platform: rebuild · Version: V26.2 (under construction) · License: [MIT](./LICENSE)
> Companion reading: [VISION.md](./VISION.md) (why it exists), [ROADMAP.md](./ROADMAP.md) (what comes next), [README.md](./README.md) (how to run it)
> This document is for outside readers who want to read the code, change the code, or judge whether this design is trustworthy. The code coordinates below are the positions measured at the time of writing; line numbers drift with iteration, symbol names are relatively stable.
> **Writing convention: every mechanism written down comes with its current implementation boundary written down alongside. What cannot be verified is not written.**

---

## 1. The architecture in one sentence

**rebuild is a goal-driven agent system orchestrated with LangGraph and advanced stage by stage through P0–P6: each stage's goal, acceptance anchors, user gate and evidence contract are fixed, while the plan and execution that reach the goal are generated autonomously by the LLM; deterministic code is responsible for exactly two things — collecting facts and verifying results.**

---

## 2. Layers

```
┌──────────────────────────────────────────────────────────────────┐
│ Frontend   React + Vite + TypeScript                             │
│   Project overview / ProjectWorkspace / stage pages P0-P6        │
│   Gate adjudication / model & resource visualization             │
│   (14 real-capability status markers)                            │
└──────────────────────────┬───────────────────────────────────────┘
                           │ HTTP /api  +  SSE event stream
┌──────────────────────────┴───────────────────────────────────────┐
│ API layer   FastAPI (32 route modules, all under /api)           │
├──────────────────────────────────────────────────────────────────┤
│ Orchestration   LangGraph StateGraph                             │
│   p0_work → p0_gate → p1_work → p1_gate → … → p6                 │
│   checkpoint (SqliteSaver) / interrupt / resume                  │
├──────────────────────────────────────────────────────────────────┤
│ Loop layer   StageLoop / NodeLoop / ReviewPass / TaskGraph       │
│   WorkAgent (build) and ValidationAgent (accept) kept separate   │
├──────────────────────────────────────────────────────────────────┤
│ Capability layer (called by agents on demand, not the main line) │
│   ModelGateway → LiteLLM adapter                                 │
│   ToolRegistry (dispatch + risk grading + gates)                 │
│   ExecutionProvider (local/container/toolchain/workspace/SSH)    │
│   HookEngine · Skill/Resource Loader · ContextAssembler          │
│   Scenario-pack Loader · MCP · capability probing                │
├──────────────────────────────────────────────────────────────────┤
│ Record layer   Evidence (files) · Trace (memory+jsonl) · Audit   │
│                Artifact · Gate (database table p_gate)           │
├──────────────────────────────────────────────────────────────────┤
│ Storage layer   SQLAlchemy 2.0 + Alembic                         │
│   SQLite (default) / PostgreSQL (optional) · workspace files     │
└──────────────────────────────────────────────────────────────────┘
```

The backend has roughly 90 service modules and 30 database tables. That is not small, but the trunk consists only of the layers above — most modules are "capabilities", not "process".

---

## 3. P0–P6: the goal-driven agent loop

This is the platform's most central design constraint, and also the part most easily misunderstood.

### 3.1 Only four things are "fixed" per stage

| Fixed item | Meaning |
|---|---|
| Stage goal | What this stage is to achieve |
| Acceptance anchors | The relatively fixed standard for judging whether it was achieved |
| User gate | Promotion to the next stage requires human authorization |
| Artifact / Evidence contract | The structural convention for output and evidence |

**Everything else — how the plan is made, which tools and commands are called, how tasks are broken down, what the artifacts say — is generated autonomously in-process by the agent (LLM) inside the stage**, and is not hard-wired as deterministic rules or as the main process line.

You can see this constraint written down explicitly in the code. Take the shared loop for P0–P3 (the module header of `backend/app/services/stage_agent_loop.py`):

- the deterministically pre-collected "fact pack" serves only as **prompting material** (help), and is **not a ceiling on reading** — the agent may read more real source code as needed, across multiple rounds;
- the stage's **structured output contract does not change**; this helper only changes *how* the text is produced (a multi-round tool loop vs a single call), never the contract;
- **there is no stage-level tool allowlist** — the full available tool set is loaded, and tool safety rests entirely on the L0–L5 grading inside `execute_tool` (L3 and above triggers an approval gate);
- no usable model, or all attempts failed → **return an honest failed result with the attempt chain attached; never fall back to rules, never fabricate completion**.

The number of loop rounds is bounded (currently a ceiling of 6 tool-call rounds, `_MAX_TOOL_ROUNDS`). When the same "tool name + arguments" combination is called consecutively up to a threshold, the model is reminded to change approach or wrap up — **only a reminder; it does not intercept**.

### 3.2 The loop skeleton

The platform reuses four existing skeletons rather than building another one per stage (`backend/app/graph/stage_loop.py`, `backend/app/services/node_loop.py`, `review_pass.py`, `task_graph_service.py`):

```
read context → stage plan → execute → self-check → Review Pass
   → rework (≤N rounds) → over the limit or high risk ⇒ escalate to a gate
   → passed ⇒ enter the promotion gate
```

### 3.3 Separation of build and acceptance identities

Acceptance is not "the builder taking another look at its own work". `backend/app/services/validation_agent.py` is an acceptance subject with an **independent identity and independent context**:

- an independent agent identity and an **independent database session**, not reusing the building agent's session;
- its input is **only artifacts persisted to disk** — it consumes only persisted references and re-reads the content from disk before verifying, and **does not read the building agent's in-process reasoning fields**;
- not up to standard → trigger a rework round and the building agent runs again; **a user gate is created only once it is up to standard**.

**Boundary**: this separation holds at the stage-acceptance layer; what it constrains is "who judges", and it does not mean the judgement itself is necessarily correct — judgement quality still depends on model and evidence quality.

---

## 4. Determinism is used in exactly two places

This is a hard constraint, not a stylistic preference:

```
Deterministic code does exactly two things:
  ① collection — clone / list files / read content / run commands / count / secret scanning
  ② verification — compile / test / diff / equivalence checking

Recognition, comprehension, planning, interpretation, risk judgement, acceptance
discretion — all of it is LLM reasoning.
```

There are two corollaries, both of which can be matched in the code:

1. **Deterministic capabilities are called by agents in the form of Skill / Tool / Expert Agent / MCP**, and are not the main stage line.
2. **Scenario knowledge and dimension knowledge are written into Skill prose; the Python side has no per-scenario if/elif branching.**

**Boundary**: what this constraint prevents is "passing rules off as intelligence". Its cost is that the stability of stage conclusions depends on the model — the wording and details of two runs on the same project will not be entirely identical. The structured contract fields are stable; the natural-language interpretation is not.

---

## 5. LangGraph as the orchestration substrate

LangGraph is the **only** orchestration substrate; no second orchestration framework is introduced.

`backend/app/graph/graph.py` assembles the `StateGraph`: each stage from P0 to P6 has two nodes — `{stage}_work` (build) and `{stage}_gate` (the promotion gate).

- **State persistence**: an async SqliteSaver checkpointer is attached at compile time, with `thread_id == run_id`.
- **Interruption and resumption**: the promotion gate uses `interrupt()` to pause for the user's adjudication, and **it can still resume after a process restart**. This is more than design intent — after the backend process was once killed unexpectedly, the graph state (stage statuses, artifacts, all gates) was intact and a single resume re-ran it successfully.
- **Recovery and retry**: `backend/app/graph/recovery.py` and `stage_retry.py` handle conflict recovery and stage retries.

**Boundaries**:
- The default store for orchestration state is a local SQLite file, and it is **not designed for multi-instance concurrency**.
- The `paused` field of `GET /graph/state` is currently always `False` (the interrupted state is not persisted into that field), so it **cannot be used as the criterion for "is it paused"**; judge from the gate records instead.
- For long-running tasks, only the heartbeat registry can **detect** a hang (`backend/app/services/heartbeat_service.py`); it **never retries, never recovers, never takes over** — this is the module's self-declared design boundary, not a description of a defect.

---

## 6. ModelGateway: unified model access

Every model call must go through `ModelGateway` (`call()` / `call_stream()`) in `backend/app/services/model_gateway.py`, and then land on a concrete provider via `LiteLLMAdapter` in `backend/app/adapters/litellm_adapter.py`.

**Hard constraint**: no direct provider connections, no hard-coded model names or endpoints. Strategy, tier and provider come from configuration and dynamic resolution.

**Behaviour when there are no valid credentials** (this is where capability-first lands at the model layer):

| Level | Behaviour |
|---|---|
| Pre-check | `stage_model_readiness()` can honestly determine unavailability **without a single network call being made**, with the candidate chain giving `not_configured` / `credential_missing` / `capability_unmet` / `candidate_ready` |
| All runtime attempts fail | Forced interruption, returning `blocked` plus "no model that is both configured and holds valid credentials", and writing an audit record |
| Each stage's trunk path | P0/P1/P2/P3, technology selection, acceptance-baseline capture and so on return `status="blocked"` plus `reason="no_model_key:…"`, and the code comments say outright "do not degrade into rule-based recognition / rule-based assessment" |
| Graph layer | The stage status is set to `blocked`, and an additional interruption gate plus audit record is created |
| **Advisory-layer exception** | The LLM advisory layer of P5/P6 honestly marks `skipped` plus `evidence_gap="llm_advisory_unavailable"` when there is no model, and is **non-blocking** — because it was never a gate to begin with |

**Boundary**: credentials are read from environment variables only and stored encrypted, and the API returns only `credential_status` (`configured / missing / invalid / redacted / not_checked`), **never echoing the plaintext**. The platform embeds no model and does not manage quota.

---

## 7. Capability seams: a capability requires all three roles present

This is the unit of analysis the platform uses to prevent "looks complete". One capability = an **interface declarer** + an **implementation provider** + a **consuming caller**, and none of the three may be missing:

```
interface declarer   defines the call contract — function signature / Protocol /
                     abstract base class / message format
implementation       the concrete code that really performs that contract's behaviour
consuming caller     the code that calls the interface on a real execution path
                     — not a "will call" in documentation or comments, but a real
                       call site that can be found by grep
```

**Two prohibitions**: adding only an implementation with no real call site whatsoever (equivalent to "looks complete" while not actually taking effect); and defining only an interface with no real implementation (the interface spins idle, calls error out or land on a stub). If any role is missing, acceptance **must not record it as implemented**.

The four seams the platform has on record (all three columns are real coordinates measured by reading the code):

| Seam | Interface declarer | Implementation provider | Consuming caller |
|---|---|---|---|
| ExecutionProvider | the `ExecutionProvider` Protocol in `services/execution_provider.py` plus the factory `get_execution_provider()` | **5** implementations: local subprocess / container / toolchain container / workspace-local / remote SSH | tool dispatch, workspace routing, P5 verification commands, acceptance baselines |
| ModelGateway | `call()` / `call_stream()` in `services/model_gateway.py` | `adapters/litellm_adapter.py` | the per-stage loops, the P4 execution worker, P3 planning, automatic review, the independent validation agent, the model self-test route |
| Registry-driven Tool | `load_schemas()` + `execute_tool()` in `services/tool_registry.py` | internal execution functions dispatched by write scope (read / workspace write / generate patch / apply patch / delegate to a provider / delegate to MCP / builtin) | the generic agent loop, the stage agent loop, the P4 execution worker |
| Registry-driven Hook | `run_hooks()` in `services/hook_engine.py` | `_BUILTIN_HOOKS` — **currently only 1 is registered**: `pre_write_policy` (blocks writes to the source directory, blocks writing suspected plaintext secrets) | **the only consumer**: the two call sites in `execute_tool()` (before a tool call, where it can block; after a tool call, advisory) |

**Boundary (important)**: the hook system **defines 10 hook points, and at present only the 2 around tool invocation are actually wired**; the other 8 have zero hits across the whole backend directory — defined but unwired, and they **must not be read as security control points that are in effect**.

---

## 8. Execution and isolation

### 8.1 Five execution providers

`ExecutionProvider` is the unified execution entry point for tools, commands, MCP and external agents, constrained by hooks + policy + gates. There are currently five implementations: local subprocess, container, **toolchain container** (pulling an SDK image on demand for a real build), workspace-local, and remote SSH.

### 8.2 Source read-only and plaintext-secret blocking

**Both classes of write blocking have code-level protection that cannot be switched off, but the nature of their boundaries differs.**

**Source read-only.** The source directory of the subject being modified is read-only. Beyond the registry-driven hook, `backend/app/services/workspace_mediator.py` provides a further **independent, unconditional** layer of protection — even with the hook switched off, a write into the source directory still cannot get through.

**Content-level plaintext-secret detection.** There is likewise a layer of protection that cannot be switched off: `backend/app/services/tool_registry.py` unconditionally calls `hook_engine.enforce_pre_write_policy()` **before** registry hook dispatch, and **does not read** `ResourceEntry.enabled`, so disabling that hook record cannot switch it off either. This mandatory check is also **fail-closed** — if the check itself raises, it is treated as `block` and reported at ERROR level, rather than failing open and letting the write through. It delegates to **exactly the same** check body as the registry hook (`_impl_pre_write_policy`), so the two call sites cannot drift in behaviour. In addition, the three entry points that can disable that hook record have each had an approval gate added.

**Boundary (stated as it is — the difference is in detection completeness, not in the number of layers)**:

- **Source read-only is a structural path judgement** — whether a write into a given directory gets through is deterministic, and within its scope it is complete.
- **Plaintext-secret detection is pattern matching** — it **cannot claim to cover every form of credential**. The pattern set has been converged into a single source of truth (`security_authorization.contains_secret()`); historically it was precisely because a private duplicate existed that a whole class of URL-embedded credential form (`scheme://user:pass@host`) was missed. That has now been merged into a single implementation, and a **structural regression test** locks in "no second copy of the patterns may appear again". But "the pattern set is currently good enough" and "the pattern set is complete" are two different things: **credentials in new forms may still go unrecognized**. Please do not treat this layer of protection as permission to write secrets into artifacts.


### 8.3 Subprocesses and containers

- The process-group termination logic after a subprocess timeout is converged into one place, `backend/app/services/subprocess_runner.py`, to avoid two similar implementations drifting apart.
- Container execution is **off by default**; it requires an explicit opt-in and mounting the Docker socket. That action is equivalent to handing control of the host's Docker to the backend process, is high risk, and is recommended only in a trusted single-machine environment.
- The permission on temporary files inside the container is currently `0755`, which is a **deliberate relaxation with an explicit reason** (the container runs as a non-root user and needs to traverse and read them). Tightening it would require changing the argument-passing approach, which is a separate design change.

---

## 9. L0–L5 risk grading and gates

### 9.1 The risk vocabulary

```
L0 read-only reference   no destructive risk, no extra authorization needed
L1 method call           pure functions, analysis scripts, generating reports, running tests
L2 controlled execution  local commands, scoped file writes, local Git
L3 external system write remote calls, external API writes, database changes
L4 system-level action   system configuration, starting/stopping services, permission changes
L5 high risk             a user gate is mandatory
```

### 9.2 Three judgement points sharing one vocabulary

| Judgement point | Basis | Consequence |
|---|---|---|
| Before tool execution | the **static risk level** assigned at tool registration (covering the full L0–L5) | L3 and above must clear an approval gate first; once approved it is re-dispatched, avoiding a double gate |
| Command content | `execution_provider._classify_risk()` plus the DENY list and regexes | a DENY hit → straight to L5, blocked before execution plus a gate created |
| Execution mode | `mode_policy.authorize_action()` | L4 and above is **never auto-approved**; the stage-promotion gate requires user confirmation in every mode |

The DENY layer is a dual channel of substring list plus compiled regexes (regex first, substring as fallback), blocking things like fork bombs, `chmod 777 /` and `chown -R root /`. Execution environment variables are scrubbed, and credentials embedded in URLs are stripped.

### 9.3 The gate types actually in use

Gates are persisted into the `p_gate` table, and the types really created in the code include at least: `stage_promotion`, `plan_presentation`, `source_pending`, `model_unavailable`, `action_approval` (tool-action approval), `l5_high_risk_command`, `desensitization_release` (release of desensitized delivery), `community_resource_introduction`, and `manual_confirmation`.

The P1→P2 promotion gate has a dedicated side effect: the technology selection is frozen into the database only upon approval. Promotion also has a hard check — when a stage has no real artifact, the promotion request is rejected (HTTP 422), preventing hollow promotion.

**Boundaries (stated honestly)**:
- **L5 commands create a gate; L4 commands currently do not** (whether L4 needs a gate is an out-of-scope extension).
- Automatic classification of runtime command content **currently produces only the four levels L1/L3/L4/L5**; L0 and L2 are defined but unused by that implementation. This does not affect the triggering of existing gates; but on the remote-SSH path the classification result is fed to the mode policy and decides from there whether to allow the action, so **changing the classification branches counts as changing an authorization threshold** and must not be done in passing.
- The gate-type list in the constants file has **no enforced validation point** in the code — `gate_type` is in fact a free-form string, and the types actually in use are more numerous than that list. This is a known implementation untidiness.

---

## 10. Evidence / Trace / Audit: three independent records

| Record | Question it answers | Implementation and medium |
|---|---|---|
| **Evidence** | On what grounds does this conclusion hold | `services/aet_service.py`; **the file system** — `evidence/{evidence_id}.json` under the project workspace (the workspace root is configurable) |
| **Trace** | What actually happened at the time | `core/trace_writer.py`; **an in-memory ring buffer (cap 10,000) plus jsonl files under the project workspace**; 15 trace types |
| **Audit** | Who approved what, on what risk level | `core/audit_writer.py`; the same media as above; 13 audit types |

None of the three is a database table. A gate record carries four fields — `artifact_refs` / `evidence_refs` / `trace_refs` / `audit_ref` — for two-way binding; adjudication writes a `gate_decision` audit record and back-fills the audit ID into the gate.

The accompanying principles:

```
No Evidence, No Decision.
No Artifact, No Completed.
No Trace, No Trusted Result.
```

**Boundaries (stated as they are)**:
- When a Trace write to disk fails, the `persistence` field is **honestly degraded** to `memory`; the same-named field for Audit is currently written **unconditionally** as `file+memory` and written before the file is flushed to disk — that is, when the project ID is missing or the write fails, that field does not match the facts. This is an inconsistency between the two writers and is a known defect.
- The in-memory buffer is capped at 10,000 records, beyond which older records are pushed out; long-term traceability depends on the jsonl files.

---

## 11. P5 verification: ten slots and the anti-fabrication gate

`backend/app/services/p5_validation_plan.py` splits verification into ten slots in three classes:

| Class | Slots | Description |
|---|---|---|
| **Hard-mandatory** (5) | produced code exists / patch exists / **P4 evidence is authentic (sha256 precondition)** / the P4 summary is parseable / the P4→P5 gate is approved | if any one is missing, the stage must not be marked complete |
| **Conditionally mandatory** (4) | build verification / run verification / tests passing / static checks | if the environment is in place, a real conclusion is mandatory; if it is not, an honest `evidence_gap` |
| **Enhancement** (1) | positive evidence that the source was not modified | not a gate |

**The point of this design is: good news cannot cover bad news.** In one run already observed, the build slot held a real "compilation succeeded" conclusion, yet because the hard-mandatory slot "P4 evidence is authentic" failed, the stage was still judged **not markable as complete** — "the build succeeded" was not used to paper over a failed evidence baseline. This is the platform's single most important positive asset.

A conditionally mandatory slot is only let through when it is `evidence_gap` **and a gate has explicitly accepted the risk**; if a "user input required" status appears, nothing is let through.

Besides the ten slots there is also a **non-gating** dimensional-capability section (browser walkthrough, behavioural equivalence, database reconciliation, business loop closure, regression comparison, performance benchmark and so on): where the capability is wired but the environment is missing, it is marked `evidence_gap` and recorded as "capability present, awaiting real verification in a suitable environment", and it **takes no part in gating and cannot flip the completion judgement**.

### 11.1 How a real build conclusion is obtained

The probing in `backend/app/services/toolchain_resolver.py` is **read-only** (it does not pull images). Build capability is judged `available` **only when**: Docker is reachable + the image is already local + a build target has been found + the target framework has a mapping. Three cases:

1. the host has the SDK → `available`;
2. no SDK but the toolchain-container channel is usable → `available` (recording the image reference and digest);
3. everything else → **`evidence_gap`, with the specific reason and a warm-up command hint written out; `available` is never fabricated**.

There is a further distinction on the execution side: an unavailable Docker daemon / image / SDK is marked `toolchain_unavailable`, and **this is not the same as "the build failed"** — in judgement it takes effect before "was the exit code 0", landing as `evidence_gap` rather than `validation_failed`.

**Boundary**: a successful container build only proves "it compiles". When the sample has no test project, the test slot is honestly marked "user input required"; when there is no runnable application the run slot is marked "not applicable". **Compilation passing is not proof of business equivalence.**

---

## 12. The scenario-pack mechanism

A scenario is a **type of modification/migration**, not a fixed enumeration.

```
Project.scenario                    ← a free-text id (not a Python Enum)
source/skills/scenarios/<id>/       ← the scenario-pack directory, discovered by a runtime scan
  ├── SKILL.md                      target-state vocabulary / migration patterns / common traps
  ├── acceptance-anchors.md         the acceptance anchors for this scenario
  ├── risk-catalog.md               the risk list for this scenario
  └── meta.yaml                     tier: typical | open; associated resource references
source/skills/scenarios/_generic/   ← the fallback pack: generic rebuilding methodology when there
                                      is no dedicated scenario pack
```

`backend/app/services/scenario_loader.py` scans the directory at runtime (`list_scenarios()` / `resolve_scenario_pack()`). **Measured: with the backend running continuously and not restarted, creating a new scenario-pack directory made the scenario appear in the API response immediately, with the prose matching word for word; deleting the directory made it stop being listed immediately.** A before/after comparison of the `*.py` / `*.tsx` change set was completely empty — adding a scenario really is a zero-code change.

There are currently three built-in typical scenario packs (`xinchuang_switch` / `modernization` / `porting`) plus one `_generic` fallback pack. **The typical scenarios are not the complete set** — the platform supports scenarios "**including but not limited to**" these three; what "typical" means is "comparatively richer supporting resources" (each comes with a target-state vocabulary, acceptance anchors, a risk list, candidate technology options and associated skills), not "the platform supports only these three". Users can create new scenario packs, and can also copy a typical pack and rewrite it; scenario packs are not read-only built-in assets.

With no dedicated scenario pack, `_generic` is used and **"no dedicated scenario knowledge" is explicitly marked**, without silently applying the terms of some existing scenario.

**Boundary (measured, important)**: scenario-pack content does enter the model context and is cited accurately, but a controlled experiment proved that when scenario-pack rules conflict with an explicitly stated **migration goal**, the model tends to follow the migration goal, points out the conflict in its reasoning, and hands the question "should the scenario pack be reassigned" back to the user. **A scenario pack is no substitute for writing the target state down clearly.**

---

## 13. Context assembly and budget

`backend/app/services/context_assembler.py` assembles context in layers (C0–C4: identity/scenario, platform conventions, Skill prose, project facts, and so on), with the recipe declaring which layers are needed and the budget.

- When the budget is exceeded, trimming happens layer by layer and an assembly manifest is emitted (including the budget spec and whether trimming occurred).
- This path used to be "declared but not consumed" — it is now genuinely wired: **measured, in one real run, that nearly twenty thousand characters of Skill prose did enter the prompt and were not squeezed out by the budget**.

**Boundary**: budget trimming may currently discard a layer the recipe declared mandatory without saying so noticeably; this is a registered improvement item (see [ROADMAP.md](./ROADMAP.md)).

---

## 14. The frontend

React + Vite + TypeScript. The core pages are the project overview, ProjectWorkspace (an IDE-style layout), the P0–P6 stage pages, gate adjudication, and model and resource visualization.

Conventions:

- **14 real-capability status markers** (such as `real_available` / `real_limited` / `credential_missing` / `not_connected` / `mock` / `static_demo` / `blocked_by_policy` / `waiting_gate` / …), on a **dual channel of colour plus Chinese text**, with the status **coming from the backend — the frontend does not guess**.
- `mock` / `static_demo` / `not_connected` must be distinguished explicitly and must not be lumped together as "available".
- Keys / tokens are desensitized throughout; the frontend does not echo them and does not request plaintext to be sent back for display.
- Functional icons use a single line-icon component; emoji are not used as functional icons.
- User-visible text is Chinese-first; technical identifiers (provider id / model name / field names) are kept as-is.

**Boundary**: some frontend interactions are still being polished, and a small number of pages have incomplete runtime integration evidence (for example, missing browser-level screen recordings). A capability marked in the interface as partially implemented or not connected means exactly that.

---

## 15. Tech stack, ports and directories

### 15.1 Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12 · FastAPI · uv · SQLAlchemy 2.0 · Alembic |
| Agent orchestration | **LangGraph** (with checkpoint / interrupt / resume) |
| Model access | ModelGateway → LiteLLM adapter layer |
| Frontend | React · Vite · TypeScript |
| Storage | SQLite (default) / PostgreSQL (optional) |
| Deployment | Docker Compose (including the controlled execution-sandbox image) |

### 15.2 Ports and directories

Ports follow a single convention; please do not introduce others in code, configuration, scripts or container orchestration: **backend 8000** (the same for development and containers), **frontend development 5173**, **frontend container 8080**.

```
backend/     FastAPI service, agent orchestration, model gateway, data models and migrations
frontend/    React + Vite frontend
source/      platform runtime resources (skills / scenario packs / cases / resource manifests)
deploy/      deployment and execution-sandbox build contexts
scripts/     operations and maintenance scripts
community/   standalone community service (not started by default; positioning and
             activation conditions in ROADMAP.md)
```

All endpoints are mounted under the `/api` prefix (the health check is `/api/health`, not `/health`). The first startup creates tables and writes seed data automatically; if the actual database lags behind the migration revision, startup reports it explicitly at ERROR level rather than handling it silently.

---

## 16. What the architecture explicitly cannot do

The boundaries scattered through the sections above, collected here so they can be read in one pass.

| Cannot do | Description |
|---|---|
| **Unattended, fully automatic migration** | Stage promotion requires a user gate, and the stage-promotion gate requires user confirmation in every execution mode. This is the orchestration layer's design, not automation awaiting completion |
| **Keep working with no model credentials** | The trunk path honestly reports `blocked` and does not degrade into a rule engine. A hard constraint, not a configuration option |
| **Give a real build conclusion with no Docker / no SDK** | Always `evidence_gap`, with the reason written out |
| **Give a verification conclusion when the environment is missing** | No browser → no walkthrough evidence; no remote host → no remote-execution evidence; no test project → the test slot says "user input required". **Seeing a lot of degradation is by design** |
| **Prove business-semantic equivalence** | The platform can only prove that it compiles, that tests pass, and that the diff is readable |
| **Multi-instance concurrency / multi-user** | No login, no accounts, no tenant isolation; orchestration state lands in local SQLite by default. **Do not expose it to the public internet** |
| **Automatically take over hung long-running tasks** | Only the heartbeat can detect a hang; it does not retry and does not recover |
| **Have all 10 lifecycle hook points in effect** | Only the 2 around tool invocation are actually wired |
| **Automatically classify runtime commands into all six levels / create a gate for L4 commands** | Classification currently produces only L1/L3/L4/L5 (L0/L2 defined but unused); command-level gates are currently created only at L5 |
| **Use the `paused` field to tell whether it is paused** | That field is always `False`; judge from the gate records instead |
| **End-to-end validation on a thousand-file-scale real project** | The complete end-to-end real run finished on the current baseline had a source base of **5 files** (a miniature sample stub); the stage traces and execution-stage PoC output on a thousand-file-scale real system came from an **earlier baseline**. The current baseline has not yet had a complete thousand-file-scale end-to-end validation — see [VISION.md](./VISION.md) section 7.6 |
| **A ready-to-use distribution** | No PyPI package, npm package or public image; build from source |

---

## 17. If you want to change the code

- The backend test suite is around 1,600 cases and can be run in full; please attach tests with your changes.
- A single orchestration substrate is a hard constraint: **do not introduce a second orchestration framework**, and do not introduce a runtime TypeScript dependency.
- For a new capability, check yourself against the three roles in section 7: **interface, implementation, real call site — missing any one means it does not count as done**.
- If a new capability is unavailable in certain environments, make it degrade honestly (`blocked` / `evidence_gap` / `not_applicable`), and **do not fall back to mock or placeholder content** — that would directly damage the only real selling point the platform has.
- For detailed development conventions see [CONTRIBUTING.md](./CONTRIBUTING.md).

---

> If you find any description in this document that does not match the code's actual behaviour, that is a defect to be corrected — please file an issue; feedback of this kind has higher priority than feature requests.

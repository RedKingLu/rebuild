# ROADMAP — What rebuild does next

> **Language / 语言**: English (this page, a translation) · [中文](./ROADMAP.zh.md)
> The **Chinese version is authoritative** for this repository's public documentation. This English page is a translation of it; where the two disagree, **the Chinese version governs**.
> See [`.i18n.yaml`](./.i18n.yaml) for the language-variant manifest. Links on this page point to the English variants.

> Platform: rebuild · Version: V26.2 (under construction) · License: [MIT](./LICENSE)
> Companion reading: [VISION.md](./VISION.md) (why it exists and what it explicitly cannot do), [ARCHITECTURE.md](./ARCHITECTURE.md) (how it is implemented), [README.md](./README.md) (how to run it)

---

## 0. How to read this roadmap

**Three reading conventions — please finish these before going further:**

**① Status comes in three tiers, and what is planned is not written up as implemented.**

| Marker | Meaning |
|---|---|
| **Implemented** | Has real run evidence; you may expect it as an existing feature |
| **Partially implemented** | The capability is present but has an explicit boundary (environment dependency, incomplete evidence, partial coverage) |
| **Planned** | Not yet implemented. **Do not expect it as an existing feature**, and it does not mean it will certainly be done |

**② No schedule is promised.** This document is organized by "priority direction"; it gives no dates and no version-number commitments. rebuild is maintained by very few people, and any date would turn into a broken promise. Items whose priority is settled come first; those whose preconditions are unmet come later.

**③ The roadmap will reorder itself because a problem was found.** The platform's core selling point is "trustworthy conclusions", so **correcting one distorted judgement takes priority over adding one feature**. If you report a defect where "the platform described something unavailable as available", it jumps ahead of every new feature.

---

## 1. Snapshot of the current state

**rebuild is currently at the stage of being runnable and able to complete an end-to-end run, but still under active construction. It is not a turnkey, fully automatic migration platform.**

**Implemented** (with real run evidence):

- Backend and frontend start locally; projects can be created and advanced through P0–P6 (P0 intake → P1 fact-base → P2 assess → P3 plan → P4 execute → P5 verify → P6 deliver), and the pipeline is trimmable.
- The trunk paths — stage orchestration, user gates, evidence and execution traces, task graph, model gateway, workspace isolation and the execution sandbox, code-hosting integration — are implemented and have self-verification evidence.
- Real builds inside a container: when the host lacks the corresponding SDK but Docker is available, the official SDK image is pulled on demand and the build is really executed in an isolated environment, producing a real compilation conclusion with detailed diagnostics.
- Detection of fabricated dependencies: existence and version-resolvability checks against package repositories, exposing dependencies invented by the model as early as possible.
- Anti-fabrication judgement: when the artifact does not hold up, the verification stage does not wave it through just because "the build succeeded" — this path has been verified effective in a real run.
- Runtime discovery of scenario packs: adding or removing a scenario-pack directory takes effect immediately, with no backend restart and no code changes.
- The backend test suite has roughly 1,600 cases and can be run in full.

**Partially implemented** (boundaries in [VISION.md](./VISION.md) section 6.2 and [ARCHITECTURE.md](./ARCHITECTURE.md) section 16): coverage of dependency checking, environment coverage of verification dimensions, observation of long-running tasks, lifecycle hook points, the decision weight of scenario knowledge.

**Points that currently require human intervention** (this is by design, not a gap): stage promotion requires user-gate authorization; modified artifacts require human review before adoption; model credentials must be supplied by you; a real build conclusion depends on your environment having Docker or the SDK.

---

## 2. Priority direction one: converge "trustworthiness" fully

This group is the current top priority. It adds no features; it narrows the confidence interval of conclusions already being made — which matters more than adding features.

### 2.1 End-to-end validation on a thousand-file-scale real project (planned)

**Current state (please understand it precisely)**:

- **The complete end-to-end real run finished on the current code baseline (P0 to P5/P6) was on a source base of only 5 files** — a miniature sample stub prepared for end-to-end acceptance. It proves the whole path can be walked and that the anti-fabrication gate really does stop an artifact that does not hold up; it **does not prove any capacity to carry scale**.
- **On an earlier code baseline, stage execution traces and execution-stage PoC artifacts were indeed produced on a real ASP.NET WebForms system of roughly 1,050 files and 84 C# source files** (dozens of modified code files and patches). That history really exists, but it belongs to an earlier baseline and was a stage-level PoC.
- ⇒ **The current baseline has not yet undergone a complete end-to-end validation on a thousand-file-scale real project.**

**Why it ranks first**: scale will simultaneously squeeze out three unvalidated failure modes — per-stage elapsed time, context-budget pressure, and recovery behaviour after an interruption partway through a long path. What is known about these three right now is "by design it should work", not "confirmed by measurement".

**What finishing it will answer**: roughly how long a real-scale modification takes, at which stage it is most likely to get stuck, and how many human adjudications are needed. At present these questions have **no trustworthy answer** — anyone who gives you a number is guessing.

> One pitfall of our own: the two samples above **share the same name** and differ two-hundredfold in scale. When citing a sample you must check its actual content rather than its name — this is a common source of distorted maturity descriptions.


### 2.2 Coverage of dependency-authenticity checking (partially implemented)

**Measured evidence already in hand (in both directions)**: when a real registry is reachable, a NuGet package invented by the model was judged `package_not_found` (HTTP 404) while 7 other coordinates in the same batch were judged `resolvable` — that is, both "identifying a fake package" and "confirming a real one" are backed by real responses.

**The line already held**: when the network is unreachable or redirected to a non-allowlisted host, the platform marks `indeterminate` and **gives no conclusion**, and explicitly records that the dependency manifest was not modified, nothing was degraded, and no replacement package was recommended. **A network failure is never misjudged as "the package does not exist".**

**Gap (planned)**: semantic version range expressions for npm / Maven (`^` / `~` / property placeholders / BOM / snapshot versions) are currently **not solved for**, and are uniformly and honestly marked "version undeterminable". Giving a definite conclusion would require bringing in a new solver dependency; that is a separate evaluation and it has not been decided whether to do it.


### 2.3 Comparative observation of produced-code volume (planned)

**Goal**: make "source-code volume vs produced-code volume" into a **non-blocking observation metric**, marking "human review required" when the ratio is abnormally low.

**Explicitly not doing**: no threshold-based interception, no wiring into gates, no change to stage-promotion judgement. **Under no circumstances will this metric cause a stage to fail or block.**

**Why no threshold**: "how low counts as abnormal" must be decided by a real-scale sample; setting a threshold before having one is guesswork. So the threshold itself depends on 2.1 being finished.

### 2.4 Several registered implementation untidinesses (planned)

None of these affect current runtime correctness, but they can mislead "someone reading the code", so they are on the record:

- Context-budget trimming may discard a layer the recipe declared mandatory without saying so noticeably.
- The persistence-marker field of audit records is unconditionally written as "file+memory", and written before the file is flushed to disk — so when the project ID is missing or the write fails, that field does not match the facts (the same-named field in trace records is implemented correctly; the two are inconsistent).
- The risk-ladder constants have a same-valued duplicate in two places (the values currently agree, so there is no defect, but it is a drift-prone pattern).
- The gate-type constant list has no enforced validation point in the code, and the types actually in use are more numerous than the list.

### 2.5 Completing runtime evidence for the frontend (partially implemented)

**Current state**: some frontend changes lack browser-level runtime evidence (screenshots or screen recordings). The pages do call the real endpoints and render real responses, but the evidence form is incomplete.

**Boundary**: this does not affect functionality; what it affects is "whether we can prove it was integration-tested".

---

## 3. Priority direction two: make open extension genuinely usable

**The scenario system has two layers — typical scenarios plus open extension — and the typical scenarios are not the complete set.** The platform supports scenarios "including but not limited to" domestic-stack switchover / software modernization / software porting. This section is about making the second layer more usable.

### 3.1 The scenario-pack ecosystem (partially implemented)

**Implemented**: adding a scenario = adding one directory, zero code changes, effective immediately at runtime (measured: no backend restart needed); users can create new scenario packs and can also copy a typical pack and rewrite it; with no dedicated pack the platform takes the generic fallback and explicitly marks "no dedicated scenario knowledge", without silently applying the terms of some existing scenario.

**Planned**:

- **Authoring guidance and templates for scenario packs** — at present, writing a high-quality new pack requires reading the existing typical packs to learn what to write.
- **Example packs beyond the typical scenarios** — types such as cloud-native transformation or splitting a monolith into microservices currently have to be written from scratch by the user.
- **The weighting problem between scenario knowledge and the migration goal** — measurement shows that when scenario-pack rules conflict with an explicitly stated migration goal, the model tends to follow the migration goal, and hands the question "should the scenario pack be reassigned" back to the user. This is not a defect, but it means **a scenario pack is no substitute for writing the target state down clearly**; how to make the relationship between the two clearer is an open design question.

### 3.2 Wiring the lifecycle hook points (partially implemented)

10 hook points are defined; at present only the 2 around tool invocation are actually wired. The other 8 are "defined but unwired" and **should not be read as security control points that are in effect**. Wiring them one by one is incremental work, pursued as actual need dictates, with no commitment to do all of them.

### 3.3 Observation and self-healing for long-running tasks (partially implemented)

**Implemented**: the heartbeat registry can **detect** that a task has hung.

**Explicit boundary**: it **never retries, never recovers, never takes over**. This is deliberate — a component that takes over automatically very easily grows into a second orchestrator, and the platform permits only one orchestration substrate to exist.

**Planned**: better visibility of hangs (the frontend being able to show "how long this task has been without a heartbeat"). Automatic takeover is **not planned**.

---

## 4. Priority direction three: let people outside get started

### 4.1 Bilingual documentation (landed)

**Approach**: a lightweight source document plus language-variant files (pairs such as `README.md` / `README.zh.md`, with a companion manifest [`.i18n.yaml`](./.i18n.yaml) at the repository root), **introducing no i18n framework and no runtime dependency**.

**Current state**: all four public documents at the repository root (this roadmap, the project entry point, the vision and the architecture) now have both a Chinese and an English variant. **The Chinese variant is authoritative** — the English variant is a translation of it, and where the two disagree the Chinese variant governs; that rule is recorded in the manifest.

**Explicit boundary**: this covers only the **public documentation** at the repository root. **User-visible text in the platform's frontend remains Chinese-first** — frontend multi-language support is a separate design change and is not brought along automatically by making the documentation bilingual. Translation covers only those four documents; the remaining public files (contribution guide, code of conduct, security reporting) are still Chinese-only, which the manifest records honestly.

### 4.2 Prebuilt distributions (planned)

**Current state**: no PyPI package, npm package or public image has been published yet; build locally from source.

**Precondition**: before distributions, there must at least be the real-scale validation conclusion from 2.1 — otherwise it amounts to turning a tool whose scale ceiling is unvalidated into a one-click install, which manufactures false expectations.

### 4.3 Multi-user and permission system (planned; preconditions unmet)

**Current state**: currently intended for **single-machine, single-user** local use, with **no login, no accounts, no tenant isolation and no permission system**. **Do not expose the service to the public internet.**

**Why it ranks later**: multi-user pulls on four threads — orchestration-state storage (currently local SQLite by default), credential ownership, workspace isolation, and the audit subject — making it a structural change. Doing multi-user before the single-user form has been validated by a real-scale project is building on quicksand.

**Trigger condition**: a real demand for multi-person collaborative use appears, and the trustworthiness of the single-user form has converged (section 2 finished).

---

## 5. Community: current form and activation conditions

This section answers "where do I find people to discuss this with". **Please read the current state first, to avoid a wasted trip.**

### 5.1 Current state: the discussion channel is not open yet

**The repository is still private at present, and GitHub Discussions is not open yet.**

- **The plan**: use **GitHub Discussions as the transitional discussion community**, **to be opened once the repository is made public**, with a first post covering the project vision and how to take part.
- **What cannot be done yet**: Discussions is not open, so **there is no usable public discussion entry point right now**, and this document provides no link either.
- **Making the repository public is itself an action requiring explicit approval**, with preconditions (security fixes and a full-history secret scan completed); it will not be done casually.

Until then, participation follows [CONTRIBUTING.md](./CONTRIBUTING.md); **for security issues use the private channel in [SECURITY.md](./SECURITY.md) and do not open a public issue**.

### 5.2 The `community/` directory: positioning and activation conditions

The repository contains a `community/` directory, which is a **standalone community service** (its own backend plus frontend, decoupled from the main platform over HTTP, a separate profile in the container orchestration, **not started by default**).

**Its status right now: on hold, not abandoned.**

| Item | Description |
|---|---|
| **What it is** | A standalone community-portal service, with zero code mixing into the main platform, startable and stoppable independently |
| **Current status** | **Construction on hold**. What is already implemented is not rolled back or deleted, but no new construction is invested in it this phase |
| **Default behaviour** | Not started by default in the container orchestration; does not affect the main platform's operation |
| **Why on hold** | Building a community portal before there are real participants is premature investment. GitHub Discussions costs nothing, accumulates content, is naturally of a piece with an open-source repository, and is sufficient for the transitional period |
| **Activation condition** | Activated when there are **stable, real external participants** and a **need to accumulate content** — that is, when discussion volume and content accumulation exceed what Discussions can carry, and someone genuinely needs a standalone portal |
| **Why it is kept** | To preserve existing assets, so that a later "grow the community" effort has something to build on rather than starting over |

**Please do not read `community/` as "abandoned dead code"**, and do not read it as "there is already a community portal". It is a built component awaiting activation on conditions.

---

## 6. Things explicitly not on the roadmap

These are not "later"; they are **explicitly not being done**:

| Not doing | Reason |
|---|---|
| A general AI office platform / general agent marketplace / general project-management platform | Beyond the positioning red line; directly conflicts with the platform's admission criteria |
| Pure consulting-style output with no source code | Violates the first admission criterion (there is source code); with no source there is nothing to verify against |
| A general automation platform for all software engineering tasks | **"General software rebuilding" is not the same as "general software engineering"** — what the platform lifts is the restriction on the type of modification; it does not bring greenfield development, requirements management, operations or test outsourcing into scope |
| Unattended, fully automatic migration | Directly conflicts with the design tenet "critical junctures are adjudicated by a human". **This is not a long-term goal on the roadmap; it is an explicit non-goal** |
| A built-in model / managing model quota | Bring-your-own credentials (BYOK) is a design choice |
| Introducing a second agent-orchestration framework | A single orchestration substrate is a hard constraint |
| Introducing a runtime TypeScript dependency | Same as above |
| Falling back to mock or placeholder content when a capability is unavailable | This would directly damage the only real selling point the platform has |
| Automatically taking over hung long-running tasks | Easily grows into a second orchestrator (see 3.3) |
| Frontend multi-language support (brought along automatically by bilingual documentation) | A separate design change requiring its own evaluation (see 4.1) |

---

## 7. What this roadmap explicitly does not promise

Even when a piece of work is written above, it **does not constitute** the following promises:

- **No dates are promised.** This document contains no schedule at all; no "expected to ship in month X" claim originates here.
- **No promise that "planned" items will certainly be done.** Priorities are reordered when new problems are found; items whose preconditions are unmet (multi-user, for example) may stay untouched for a long time.
- **No promise of backward compatibility.** The platform is still under active construction; data models, API fields and artifact structures may all change. There is currently **no stable API commitment** and no versioned interface contract.
- **No promise that finishing 2.1 equals "production-ready".** Thousand-file-scale validation answers "can it run to completion"; it does not answer "will it work for your project" — the latter depends on the shape of your code, how clearly the target state is stated, and your verification environment.
- **No promise to solve business-semantic equivalence.** The platform can prove that something compiles, that tests pass, and that the diff is readable; **semantic equivalence will always need your domain knowledge to vouch for it** — this is not a limitation the roadmap will eliminate, but a division of labour.

---

## 8. How to influence this roadmap

In descending order of how much it helps the project:

1. **Report defects where "the platform described something unavailable as available".** Problems of this kind outrank every new feature. Please include the status markers you saw and your actual environment.
2. **Try it on a real project and report where you got stuck.** Failure reports from large-scale projects are especially welcome — that is exactly what section 2.1 lacks.
3. **Contribute a scenario pack.** A well-written custom scenario pack (target-state vocabulary + acceptance anchors + risk list) is of high value to others, and requires no code changes at all.
4. **Submit code.** Please read [CONTRIBUTING.md](./CONTRIBUTING.md) first, along with [ARCHITECTURE.md](./ARCHITECTURE.md) section 7 (the three roles of a capability seam) and section 17 (what to know before changing code).

For security issues use the private channel in [SECURITY.md](./SECURITY.md), and **do not open a public issue**.

---

> The writing conventions of this document match [VISION.md](./VISION.md) and [ARCHITECTURE.md](./ARCHITECTURE.md): status in three tiers; every capability written down comes with its boundary written down alongside; what cannot be verified is not written. If you find a description here that does not match reality, please file an issue.

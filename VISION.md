# VISION — Why rebuild exists

> **Language / 语言**: English (this page, a translation) · [中文](./VISION.zh.md)
> The **Chinese version is authoritative** for this repository's public documentation. This English page is a translation of it; where the two disagree, **the Chinese version governs**.
> See [`.i18n.yaml`](./.i18n.yaml) for the language-variant manifest. Links on this page point to the English variants.

> Platform: rebuild · Version: V26.2 (under construction) · License: [MIT](./LICENSE)
> Companion reading: [README.md](./README.md) (how to run it), [ARCHITECTURE.md](./ARCHITECTURE.md) (how it is implemented), [ROADMAP.md](./ROADMAP.md) (what comes next)
> This document is written for outside readers and answers only four questions: why it exists, whose problem it solves, what it can currently do, and **what it explicitly cannot do**.

---

## 1. In one sentence

**rebuild turns "convert an existing piece of software into another form" into an engineering process with stages, with evidence, and with a human adjudicating — rather than one batch generation run that gambles on luck.**

---

## 2. Why it exists

A great deal of software still running in production faces forced modification: the database has to change, the runtime version reaches end of support, the operating system or CPU architecture has to be replaced, the framework is so old that nobody maintains it, a cross-platform port is needed. Work of this kind shares two traits:

- **Risk is asymmetric.** Getting one spot wrong costs far more than getting one spot right gains.
- **The business cannot stop.** There is no "tear it down and start over" option.

The two common ways this is handled in practice both have obvious defects.

**Approach one: modify it by hand, file by file.** The conclusions are trustworthy, but it is expensive and slow, and the judgements produced along the way (why this replacement was chosen; where a known risk was knowingly let through) usually exist only in a few people's memories, and are lost at handover.

**Approach two: hand the whole codebase to a large model for batch modification.** Fast, but it produces a particularly dangerous artifact — **code that looks complete but does not hold up**. The model will invent dependency packages that do not exist, offer equivalence judgements it never verified, and, when it cannot finish, emit a well-worded completion report. From the artifact alone, the party doing the modification cannot tell which parts are trustworthy.

rebuild starts from this premise: **the failure mode of approach two is not a model-capability problem but a process that lacks constraints**. If a system will honestly say "cannot do this" when its capability falls short, if every conclusion has reviewable evidence hanging off it, and if the decision is handed back to a human at the critical junctures, then using AI for this kind of modification becomes acceptable.

So what rebuild prioritizes is not "how fast it gets modified", but **"once it is modified, can you verify it, can you trace it, and can you tell where it still cannot be trusted"**.

---

## 3. Whose problem it solves

### 3.1 Who it is for

rebuild is for technical teams and individuals who **have source code in hand and need to change its form**:

| User | Their actual problem |
|---|---|
| The technical lead responsible for a replacement/upgrade project | Needs the workload, risk points and open items stated up front in order to get the project approved and scheduled — rather than discovering them while doing the work |
| The engineer actually doing the modification | Needs a task breakdown that can be executed, plus reviewable grounds for each change — rather than a black-box artifact |
| The person who has to sign off on the result | Needs to know what evidence a "pass" was decided on, and which parts were not verified at all |

### 3.2 Whose problem it does not solve

- Assessment and consulting needs with no source code — the platform has nothing to work from and will refuse plainly rather than produce something under duress.
- Building a new system from scratch — with no "before" state there is no decidable target state to modify toward.
- Everyday coding assistance — that is the domain of an in-editor AI assistant; rebuild advances stage by stage per project, at an entirely different granularity.

---

## 4. What the platform believes (design tenets)

These five determine the fundamental difference between rebuild and an automation script built on "it runs, that's enough". They are design choices, and they do sacrifice some convenience.

**1. capability-first: when a capability falls short, report it honestly; never fake success.**

When valid model credentials are missing, when there is no bindable source code, when the environment is not in place, or when evidence is insufficient, the platform returns `blocked` or `evidence_gap` rather than degrading into placeholder content, mock data or rule templates to pass itself off as finished. A conclusion that says "I did not manage this, because X" is worth more than a pretty artifact that cannot be traced.

**2. No Evidence No Completed.**

Every "completed" must have evidence hanging off it. Artifacts, verification records and execution traces can be reviewed after the fact. When the judgement is "not passed", it is not permitted to wave it through on "looks right".

**3. Reasoning belongs to the LLM; collection and verification belong to deterministic code.**

Recognition, comprehension, planning, risk judgement and acceptance discretion go to agent reasoning; cloning code, listing files, running commands, counting, compiling, running tests and producing diffs go to deterministic tools. Neither impersonates the other — deterministic code does not pretend to understand the business, and the model does not pretend that compilation passed.

**4. Critical junctures are adjudicated by a human.**

Stage promotion and high-risk actions are gated: a human must have seen the evidence and authorized it. This is a design choice, not automation that has yet to be finished — responsibility for modifying code should not rest with a model alone.

**5. BYOK (bring your own model credentials).**

The user supplies the model API keys; they are read from environment variables only and stored encrypted, and the API returns only credential status, never echoing the plaintext. The platform embeds no model of its own and does not manage your quota.

---

## 5. What kind of work suits rebuild

### 5.1 Typical scenarios (the platform's focus, with comparatively richer supporting resources)

| Scenario | Identifier | Description |
|---|---|---|
| Domestic-stack switchover | `xinchuang_switch` | Migration to domestic hardware, software and database stacks |
| Software modernization | `modernization` | Version modernization of legacy runtimes, frameworks and frontend components |
| Software porting | `porting` | Porting across platforms and runtimes |

These three are the directions the platform **invests in most heavily**: each comes with a target-state vocabulary, acceptance anchors, a risk list and candidate technology options — noticeably thicker resources than the generic fallback.

### 5.2 Open extension: including but not limited to the three above

The scenario system is **open**. The typical scenarios above are **not the complete set**; the platform supports scenarios "**including but not limited to**" these three:

- You can **add a custom scenario pack**, and you can also **copy a typical scenario pack and rewrite it for your own situation** — scenario packs are not locked-down, read-only built-in assets.
- The intended shape for adding a scenario is "**drop in one scenario-pack directory and it takes effect**", not editing an enumeration or a dropdown constant in the code.
- With no dedicated scenario pack available, the platform falls back to a generic rebuilding methodology and **explicitly marks "no dedicated scenario knowledge"**; it will not silently apply the terms of some existing scenario (for example the domestic-stack one).

Types such as cloud-native transformation, splitting a monolith into microservices, architectural decoupling, dependency de-risking and cross-cloud migration are all added through the same mechanism; the platform does not have to "support" one first.

### 5.3 The three admission criteria: the scenarios are open, admission is not

For any scenario (typical or custom) to enter the pipeline, all three must hold **at once**:

1. **There is source code** — real code that can be obtained and read.
2. **There is a decidable target state** — you can state clearly "what counts as success".
3. **The output is machine-verifiable** — the result can be checked by compilation, tests, diff, or equivalence checking.

If any one is missing, the platform will say plainly that "this is not a good fit for rebuild". These three criteria simultaneously block two things: the platform sliding into a "does everything" general automation tool, and the verification path being hollowed out by tasks that cannot be verified.

---

## 6. What it can currently do

The platform advances a project through seven trimmable stages, P0–P6: **P0 intake → P1 fact-base → P2 assess → P3 plan → P4 execute → P5 verify → P6 deliver**. The pipeline is trimmable — for an assessment only, for instance: `P0 → P1 → P2 → P6`.

Status is marked in three tiers, and **what is planned is not written up as implemented**.

### 6.1 Implemented, with real run evidence

| Capability | Description |
|---|---|
| Source-code intake | Import from a local directory / Git repository / ZIP archive / GitHub repository; workspace isolation; read-only protection of the source directory |
| Project fact-base | Structure, tech stack, dependencies, build and run procedures, configuration, risk leads |
| Migration assessment and planning | Multi-dimensional assessment report, risk list, open decision items; forming a plan and a task graph |
| Controlled modification execution | Node-by-node execution along the task graph, producing code and patches; writes are constrained by policy and gates |
| **Real builds inside a container** | When the host lacks the corresponding SDK but Docker is available, the official SDK image is pulled on demand and restore/build is really executed in an isolated environment, producing a real compilation conclusion with detailed diagnostics |
| **Detection of fabricated dependencies** | Existence and version-resolvability checks against NuGet / npm / Maven, exposing dependency packages invented by the model as early as possible |
| Anti-fabrication judgement | When the artifact does not hold up, the verification stage does not wave it through just because "the build succeeded"; this path has been verified in a real run to honestly stop it |
| Stage gates, interruption and resumption | Stage-promotion gates are persisted and adjudicable; orchestration state is persisted and can resume after a process restart |
| Evidence / Trace / Audit | Three independent records run through the trunk path and can be reviewed afterwards |
| Runtime discovery of scenario packs | Adding or removing a scenario-pack directory takes effect immediately, **with no backend restart**, and with no code changes |
| Unified model access | All model calls go through the unified gateway; no direct provider connections, no hard-coded model names or endpoints |

### 6.2 Partially implemented (the capability is present, but its boundary is explicit)

| Capability | Current state and boundary |
|---|---|
| Coverage of dependency-authenticity checking | When a real registry is reachable, there is measured evidence in both directions: a NuGet package invented by the model was judged `package_not_found` (HTTP 404), while 7 other coordinates in the same batch were judged `resolvable`. **Boundary**: when the network is restricted or redirected to a non-allowlisted host, the platform marks `indeterminate` and **gives no conclusion** (holding the line of "do not misjudge a network failure as the package not existing"); semantic version range expressions for npm / Maven are currently **not solved for**, and are honestly marked "version undeterminable" |
| Coverage of verification dimensions | Capabilities across build, test, static check, behavioural equivalence, browser walkthrough, database reconciliation and performance benchmark dimensions are wired up, but **when the environment is not in place they degrade honestly**; what actually executes depends on your environment |
| Observation of long-running tasks | A heartbeat registry can **detect** that a task has hung, but it **does not take over automatically and does not retry automatically** |
| Lifecycle hook points | 10 hook points are defined; at present **only the 2 around tool invocation are actually wired**, and the rest are defined but unwired |
| Decision weight of scenario knowledge | Scenario-pack content does enter the model context and does get cited, but measurement shows its **decision weight is lower than that of an explicitly stated migration goal** — a scenario pack is no substitute for writing the target state down clearly |

### 6.3 Planned (not yet implemented; do not expect it as an existing feature)

See [ROADMAP.md](./ROADMAP.md). Mainly: end-to-end validation on a thousand-file-scale real project, a multi-user and permission system, prebuilt distributions, and a standalone community portal.

---

## 7. What it explicitly cannot do

**This section matters more than section 6.** If you read only one section, read this one.

### 7.1 It is not "submit a repository URL and come back to collect the goods"

- **There are user gates between stages.** Stage promotion requires a human to review the evidence and authorize (for example from fact-base into assessment, or from execution into verification). This is not automation awaiting completion; it is a deliberately retained decision point. A stage that has not been authorized will not move forward on its own.
- **Modified artifacts require human review before adoption.** The platform's job is to produce the modification and to expose where it does not hold up — not to sign off on your behalf.
- Therefore **there is no such thing as an unattended pipeline** here. If that is what you need, rebuild is not currently suitable.

### 7.2 With no valid model credentials the platform stops; it does not degrade

With no valid key, capabilities involving recognition and reasoning **honestly return `blocked`**. The platform will **not** fall back to a rule engine, a template or placeholder content to pass itself off as finished. This is a hard constraint, not a configuration option. The platform also embeds no model of its own.

### 7.3 A real build conclusion depends on your environment

There are three cases for P5's real build conclusion, and only the first two can yield a real compilation conclusion:

1. the host machine has the corresponding SDK;
2. the host machine has no SDK, but **Docker is available** and the corresponding SDK image can be pulled;
3. everything else (Docker unreachable, image not present locally, no framework mapping, no build target found) — **honestly marked `evidence_gap` with the specific reason written out; a "build passed" is never fabricated**.

In other words: **without usable Docker or a local SDK you will not get a real build conclusion** — you will only get a record explaining why you cannot.

### 7.4 Seeing a lot of "degraded" markers is by design, not a fault

Capability probing is deterministic: with no browser automation installed there is no browser-walkthrough evidence; with no reachable remote host there is no remote-execution evidence; with no test project there is no tests-passing evidence. All such cases are marked `evidence_gap` and are **non-blocking**, while also recording "capability wired, awaiting real verification in a suitable environment".

**Please do not read a screen full of `evidence_gap` as the platform being unfinished** — it means the platform refuses to make up a conclusion when the preconditions are missing. Conversely, if you see a screen full of "passed" in an environment where nothing is installed, that is when you should file a bug.

### 7.5 Machine verification ≠ a guarantee of business equivalence

Compilation passing, tests passing and a readable diff **do not amount to** fully equivalent business semantics. Boundaries already observed in practice: a successful container build only proves "it compiles"; when the sample has no test project the test slot is honestly marked "user input required"; run verification is marked "not applicable". **Semantic equivalence still needs your domain knowledge to vouch for it.**

### 7.6 The boundary of validated scale

Please read this one carefully; it determines whether your expectation of the platform's maturity is correct.

**The complete end-to-end real run finished on the current code baseline (P0 all the way to P5/P6) was on a source base of only 5 files** — a miniature sample stub prepared specifically for end-to-end acceptance (one page + backend code + configuration + dependency manifest). What it proves is that "the whole path can be walked, and the anti-fabrication gate really does stop an artifact that does not hold up"; it **does not prove any capacity to carry scale**.

**On an earlier code baseline, stage execution traces and execution-stage PoC artifacts were indeed produced on a thousand-file-scale real system** — the subject was a real ASP.NET WebForms system of roughly 1,050 files, 84 C# source files and about 50 package dependencies, and dozens of modified code files and patches were produced. That history is real, **but it belongs to an earlier baseline and was a stage-level PoC — not a complete P0→P5 end-to-end validation on the current baseline**.

⇒ **The accurate conclusion: the current baseline has not yet undergone a complete end-to-end validation on a thousand-file-scale real project.** Its elapsed time, context pressure, and recovery behaviour after an interruption partway through a long path are all known unvalidated territory. This is also the current top-priority work (see [ROADMAP.md](./ROADMAP.md)).

> One pitfall of our own, offered so you can judge similar claims: the two samples above **share the same name** and differ two-hundredfold in scale. When citing a sample across rounds you must check its actual content, not just its name — this sort of "same name, different thing" is a common source of distorted maturity descriptions.


### 7.7 Limits of the operating form

| Limit | Description |
|---|---|
| Single-machine, single-user | **There is no login, no accounts, no tenant isolation and no permission system. Do not expose the service to the public internet.** |
| In-container execution is off by default | Requires an explicit opt-in and mounting the Docker socket (equivalent to handing over control of the host's Docker); recommended only in a trusted single-machine environment |
| No prebuilt distributions | No PyPI package, npm package or public image has been published yet; build locally from source |
| Interface language | User-visible text inside the platform is predominantly Chinese; technical identifiers are kept as-is |

### 7.8 The implementation boundary of risk grading

The platform defines the full L0–L5 risk vocabulary, and the static risk level assigned at tool registration covers all six levels and decides from them whether a gate is required. However, **automatic classification of runtime command content currently produces only four of those levels** (L0 and L2 are defined; the current classification implementation does not use them). This is the implementation reality stated as it is; it does not affect the triggering of existing gates. Changing it counts as changing an authorization threshold, and it will not be altered "in passing".

---

## 8. Goals that are not this project's

The following directions are explicitly **out of scope** and will not be extended into just because they are "on the way":

1. a general AI office platform;
2. a general agent marketplace;
3. a general project-management platform;
4. a pure consulting platform with no source code;
5. a general automation platform for all software engineering tasks;
6. a platform that showcases agent capability without closing the loop on the modification.

**"General software rebuilding" is not the same as "general software engineering".** What the platform lifts is the restriction on the *type* of modification (domestic-stack / modernization / porting / cloud-native / …); it has not brought greenfield development, requirements management, operations, or test outsourcing into scope — the three criteria in section 5.3 are exactly that dividing line: greenfield development lacks the "source-code precondition + decidable target state", and operations lacks a "machine-verifiable modification output", so neither meets admission.

---

## 9. License and participation

- License: [MIT](./LICENSE), copyright attribution `withwind`.
- For how to take part see [CONTRIBUTING.md](./CONTRIBUTING.md); for security issues use the private channel in [SECURITY.md](./SECURITY.md) and do not open a public issue.
- For the current state of discussion channels see the "community" section of [ROADMAP.md](./ROADMAP.md).

---

> Writing conventions for this document: capabilities are graded in three tiers — implemented / partially implemented / planned; every capability written down comes with its boundary written down alongside. If you find any description here that does not match actual runtime behaviour, that is a defect to be corrected — please file an issue; feedback of this kind has higher priority than feature requests.

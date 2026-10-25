# Product.md — Team Work Contract
### Data Scientist Agent (Schema-Grounded Text-to-SQL + Analysis)

| | |
|---|---|
| **Source design** | `DESIGN_DETAIL.md` (Sections A–F). If this file and the design disagree, the design wins; raise a PR to fix this file. |
| **Owner of this contract** | Team Lead (Ganesh) |
| **Team** | 4 members: 1 Frontend, 1 Analysis, 2 Backend |
| **Status** | v2 draft, to be signed off by the Lead and every member before work starts |

---

## 1. Purpose

The design describes a **controlled analytical workflow**: route → ground in the real schema → generate or compile SQL → validate by execution → run a tested statistical/ML module → write the answer only from recorded evidence.

This contract does three things:

1. Splits the work so each member owns a clear, non-overlapping part.
2. Fixes the **interfaces** between parts (Section 5), so people can build in parallel against mocks and stubs.
3. Defines what "done" means for each owner, and which prototype tag (`v0.1` to `v1.0`) each deliverable lands in.

**Project principle (everyone must follow it):** *Generate only against the retrieved schema; validate every query by execution; measure against a naive baseline. A model may choose, draft and explain; it never computes a number, and it never decides whether a query is safe.*

---

## 2. Team and roles

| ID | Role | Member | Mission in one line |
|---|---|---|---|
| **FE-1** | Frontend | Abdul | The whole web app: onboarding screens, Ask flow, Answer and Evidence view, Trace view, Metrics and Review screens |
| **AN-1** | Analysis and Recommendation | Swathi | Anomaly, Driver and Risk Analysts, the Advisor, and the planted ground truth that tests them |
| **BE-1** | Backend A: Query-time Pipeline | Ganesh (also Team Lead) | API, Orchestrator, and the question-to-validated-rows pipeline (steps 1 to 10), plus the evaluation harness |
| **BE-2** | Backend B: Knowledge Onboarding and Trust | Vishal | The first-connection pipeline that builds the vector DB and knowledge graph, the knowledge stores, and the trust layer (Ledger, Synthesizer, Fact Checker) |

**Team Lead (Ganesh, who is also BE-1):** arbitrates ownership disputes, approves contract changes, merges release tags, controls access to the locked held-out set, and is the approver for anything touching the hard safety gates.

**Because the Lead is also BE-1, nobody reviews their own work:** wherever Section 3, 6 or 7 names the Lead as reviewer of a component BE-1 owns (Guard, Validator, Executor, evaluation harness, BE-1 prompts, BE-1 changes to `contracts/`), the review is done by **BE-2 (Vishal)** instead. Where the Lead reviews other people's work (BE-2's Fact Checker and Golden Store, AN-1's Advisor, FE-1's tiers), Ganesh keeps that role.

**Contract partners (who answers whose questions within one working day)**

| Pair | Why |
|---|---|
| FE-1 and BE-1 | API, run events, answer envelope, trace events |
| FE-1 and AN-1 | How analysis output is drawn (charts, grades, recommendations) |
| FE-1 and BE-2 | Onboarding, join approval, Golden Store and Registry review screens |
| BE-1 and BE-2 | Knowledge-store interfaces, ledger and fact-checker calls |
| BE-1 and AN-1 | Analyst calling convention, typed inputs from query results |
| AN-1 and BE-2 | Planted-truth injection into the generated warehouse, Action Library storage |

---

## 3. Ownership map (design component to owner)

| Design component | Step | Owner | Backup reviewer |
|---|---|---|---|
| HTTP API and run-event streaming (all routes) | all | **BE-1** | FE-1 |
| Orchestrator (typed workflow, budgets, retries) | all | **BE-1** | BE-2 |
| Guard | 1 | **BE-1** | BE-2 |
| Router and Extractor | 2 | **BE-1** | AN-1 |
| Planner (validated against Analyst registry) | 3 | **BE-1** | AN-1 |
| Metric Resolver | 4 | **BE-1** | BE-2 |
| Answerability Checker | 5 | **BE-1** | BE-2 |
| Schema Retriever & Perceptor (`src/schema_retriever/`, online hybrid retrieval, concurrent value search, join expansion) | 6 | **BE-1** | BE-2 |
| SQL Compiler (`src/sql/compiler.py`, Zero-LLM fast path for governed metrics) | 7a | **BE-1** | BE-2 |
| NL2SQL Generator (`src/sql/generator.py`, 4-stage pipeline with ICL exemplar routing) | 7b | **BE-1** | BE-2 |
| Validator & Dual-Mode Toggle (`src/sql/validator.py`, Benchmark mode for BIRD vs Enterprise mode for warehouse safety) | 8 | **BE-1** | BE-2 |
| Repairer & Reflector (`src/sql/repairer.py`, bounded 2 attempts, same schema, reflection scoring) | 8b | **BE-1** | BE-2 |
| Multi-Datasource Executor (`src/gateway/`, `src/sql/executor.py`, read-only pool, timeout, row cap) | 9 | **BE-1** | BE-2 |
| Reconciler (`src/sql/reconciler.py`) | 10 | **BE-1** | BE-2 |
| Cache (`src/cache.py`, exact and slot-based) | all | **BE-1** | BE-2 |
| Evaluation harness (`eval/eval_harness.py`, Baseline A, Baseline B 4-stage engine, ablation ladder) | D | **BE-1** | BE-2 |
| **Warehouse Onboarding Pipeline** (first connection and scheduled refresh) | offline | **BE-2** | BE-1 |
| Metadata Catalog and column profiles, sensitive-column masking | offline | **BE-2** | BE-1 |
| Join Graph, join classifier, knowledge graph | offline | **BE-2** | BE-1 |
| Vector DB (dense index), keyword index, Value Index | offline | **BE-2** | BE-1 |
| Table trust features | offline | **BE-2** | BE-1 |
| Metric Registry (storage, change workflow, seed content) | 4, 10 | **BE-2** | BE-1 |
| Golden Store (storage, promotion workflow) | 7b | **BE-2** | Lead |
| Trace Store | all | **BE-2** (storage), **BE-1** (emits events) | FE-1 |
| Evidence Ledger | 12 | **BE-2** | AN-1 |
| Synthesizer | 13 | **BE-2** | FE-1 (reads output) |
| Fact Checker (incl. blocked causal wording) | 14 | **BE-2** | Lead |
| Synthetic warehouse generator (schema, scale, mess, distractors, unanswerable asks) and two snapshots | data | **BE-2** | AN-1 |
| Deployment, secrets, environments | F2 | **BE-2** | BE-1 |
| Anomaly Analyst | 11 | **AN-1** | BE-1 |
| Driver Analyst | 11 | **AN-1** | BE-1 |
| Risk Analyst | 11 | **AN-1** | BE-1 |
| Advisor and Action Library content | 11b | **AN-1** (module and content), **BE-2** (storage) | Lead |
| Analyst registry and module contract tests | 11 | **AN-1** | BE-1 |
| Planted ground truth: anomalies, drivers, confounders, negative controls, churn process, manifest, module scoring | C3 | **AN-1** | BE-2 |
| Entire web app (all screens) | UI | **FE-1** | BE-1 (API), AN-1 (evidence views), BE-2 (onboarding and review screens) |
| `prompts/` files | all | each owner for their own component | Lead (BE-2 for BE-1's prompts) |

**Rule:** one component, one owner. If you need a change in someone else's component, open an issue or PR to them. Do not edit it silently.

**Question-set authoring** (design D2), so the dev set is not left to one person:

| Category | Author |
|---|---|
| Governed lookups, multi-table joins and messy values, near-miss, ambiguous, injection and unsafe, scale ladder | BE-1 |
| Anomaly, root cause, risk, recommendation, causal-overclaim | AN-1 |
| Out-of-schema refusals (with paraphrases), onboarding and knowledge-store tests | BE-2 |

The locked held-out set is held by the Lead and released to the evaluation runner only for the final run.

---

## 4. Work packages per member

Tags refer to Section 8.

### 4.1 FE-1: Frontend (whole web app)

One person owns the entire UI, so work is tiered. **P0** must exist for the tag shown; **P1** is planned; **P2** is cut first if time runs short (Lead decides at each tag).

**Screen groups**

| Group | Screens and components | Tier |
|---|---|---|
| **Foundation** | Scaffold, routing, layout, design system (light/dark), API client generated from `contracts/`, mock-server wiring, accessibility baseline | P0 (v0.1) |
| **Ask flow** | Question box with examples, streaming progress from run events, recent runs | P0 (v0.1) |
| **Non-answer outcomes** | Clarification screen (shows `what_understood` and `what_needs_clarification`, user replies inline), Refusal screen (states exactly what is missing and the closest answerable question), Blocked screen, Error and "insufficient evidence" screens | P0 (v0.2) |
| **Answer and Evidence view** | Answer page in the order of design B12: route and reason, conclusion, scope, evidence, method, caveats, next action, run metrics; route badge; "ungoverned metric" label; ledger-linked numbers; evidence-grade badges 0 to 4 with allowed wording; causal-boundary note (E5) always shown on driver answers | P0 (v0.3 basic, v0.5 full) |
| **Analysis visuals** | Actual vs. expected chart with interval (anomaly); ranked driver bars with share, interval and sample size; segment risk table with revenue at risk, calibration note, limits; recommendations panel with 2 to 3 options, assumptions, trade-offs, sensitivity, and Approve / Reject buttons | P0 (v0.4 to v0.5) |
| **Onboarding screens** | Connect-warehouse form (secrets never echoed back), onboarding progress by stage, onboarding report, **join-edge review** (approve or reject proposed joins), sensitive-column review | P0 (v0.2) |
| **Trace view** | Step-by-step trace (design B3 table), SQL viewer with status and timing, repaired-vs-original diff, retrieved schema and join path, validator panel (checks 1 to 10), reconciliation panel, run metrics | P0 basic (v0.2), P1 full (v0.6) |
| **Metrics dashboard** | Hallucination rate, result accuracy, module scores, refusal precision/recall, faithfulness, p50/p95 latency and cost per route and stage; pass-rule scoreboard showing the six items of D4 separately (never an averaged score); every figure labelled with model name and version, commit, snapshot ID and date; targets visually distinct from measured values | P1 (v0.6) |
| **Review screens** | Golden Store candidate review, Metric Registry change review (diff, approve / reject), simple role flag | P1 (v0.6) |
| **Extras** | Answer export (Markdown or PDF), run comparison vs. Baseline A, scale-ladder and ablation charts | P2 |

**UI duties (non-negotiable)**
- If the API returns a number without a `claim_id`, show it flagged as "unsupported" and log it. Never render it silently.
- Never display raw sensitive values; masked values arrive masked.
- Never present a target as a result.

**Done when:** every `status` in the envelope (5.2) has a screen; every P0 screen works against mocks and the real API; a user can complete connect, ask, clarify, read answer, open trace, and approve or reject a recommendation end to end.

### 4.2 AN-1: Analysis and Recommendation

**Shared module contract:** typed input tables plus parameters in; typed output with numbers, intervals, flags, assumptions and an **"insufficient evidence"** state out. No free-form code execution. A new module enters only through the Analyst registry with contract, tests and evaluation cases (design B10).

**Anomaly Analyst (B10.1)**
- Series prep at the question's grain; drop incomplete boundary periods; calendar features.
- Seasonal baseline with prediction interval, fitted on history excluding the tested period; robust median/MAD fallback when history is short (and say so).
- Scoring (actual vs. expected, % deviation, robust score, persistence); change-point check (for example PELT); multiple-testing control when scanning many series (Benjamini-Hochberg or stricter); volume guard.

**Driver Analyst (B10.2)**
- Metric tree and contribution decomposition that **reconciles** to the total; price/volume/mix for value metrics.
- Bootstrap intervals, sample sizes, small-cell flags; co-movement tests; difference-in-differences style comparison group where one exists; cap on dimensions and depth, with correction for candidates examined.
- Ranked drivers with share, interval, sample size, **evidence grade 0 to 4** and the data that would strengthen the claim.

**Risk Analyst (B10.3)**
- Explicit churn definition (N days after cut-off, from the Metric Registry); one-time-buyer share stated.
- Leakage-safe features from before the cut-off; **chronological** splits.
- Heuristic and logistic baselines vs. gradient-boosted model; the complex model ships only if it beats the baseline on the hold-out.
- PR-AUC with prevalence, ROC-AUC, Brier, calibration, lift and precision at top-k; segment aggregation with expected revenue at risk and resampling intervals; importance reported as association, not cause.

**Advisor (B10.4)**
- Action Library schema and curated starting content (BE-2 stores it).
- Ranking = impact x evidence confidence x feasibility, with impact from quantified data, not model opinion.
- 2 to 3 options with assumptions, trade-offs, sensitivity, monitoring, and the data that would change the ranking. Weak evidence means recommend the cheapest information-gathering step. The agent recommends; a person approves.

**Planted ground truth (design C3), implemented on top of BE-2's generator**
- Planted anomalies (level shift, one-week spike, short dip, gradual decay) with known start, size and unit; planted drivers with true share; confounders; negative controls (ordinary weeks, holiday and promotion peaks, small volumes); documented stochastic customer behaviour process for churn labels; seeded SQL traps (fan-out, cancelled orders, duplicates) with known answers.
- The **ground-truth manifest** in a format agreed with BE-2 in week 1; readable only by scoring code.
- Scoring scripts and module baselines: fixed +-2 sigma rule (anomaly), "largest absolute change" (driver), heuristic segmentation (risk).

**Cross-cutting**
- Unit tests for each module: sparse data, zero baseline, empty segments, short history.
- Example output JSON for every module and every verdict in `contracts/examples/analysts/` in week 1, so FE-1 and BE-1 can start immediately.

**Done when:** each module passes its unit tests, returns the contract shape for all edge cases, and meets its threshold against planted truth on the development set (design D4, item 3).

### 4.3 BE-1: Backend A, Query-time Pipeline

**Platform**
- **HTTP API** with every route in 5.1, OpenAPI/JSON Schema published in `contracts/`, and the run-event stream.
- **Orchestrator:** the fixed typed workflow of design B2; per-query budgets for tokens, queries and wall time; retries; one trace event per step; model tiering (small model for routing assistance, slot filling and narration; stronger model for complex multi-table SQL and repair); fallback to the Compiler path when a model is unavailable, otherwise a clear error, never a guess.
- **Cache:** exact-match and slot-based plan cache keyed on normalised metric, dimension, filters and time range (not raw embedding similarity); invalidated on schema or definition change (a new `catalog_version` from BE-2) and scoped by user permission.
- **Mock backend:** a fixture-replay server in week 1.

**Pipeline steps**
- **Guard (1):** read-only and injection checks; database text is data, never instructions.
- **Router and Extractor (2):** trained lightweight classifier + rules + model slot proposal; code decides; disagreement lowers confidence; clarifying question below threshold. Routing accuracy and confusion matrix reported.
- **Planner (3):** model drafts, code validates against the Analyst registry; depth cap; fallback to single module or clarification.
- **Metric Resolver (4)** and **Answerability Checker (5):** consult the Metric Registry on every question; refusal names the missing attribute.
- **Schema Retriever & Perceptor (6):** keyword + dense hybrid with reciprocal rank fusion, cross-encoder rerank, and concurrent fuzzy value searching (`src/schema_retriever/`). Join expansion over approved edges, column pruning, value linking with confidence, column warnings, retrieval-confidence refusal.
- **SQL Compiler (7a):** deterministic Jinja/AST compilation (`src/sql/compiler.py`) for fully governed metrics (no model call, sub-350ms, same SQL every time).
- **NL2SQL Generator (7b):** 4-stage pipeline (`src/sql/generator.py`: Perceptor, Generator with ICL exemplar routing, Validator, Reflector) for ad-hoc and exploratory queries, using skeleton generation and divide-and-conquer strategy.
- **Validator (8):** all ten checks of B8 with **Dual-Mode Toggle** (`src/sql/validator.py`): Benchmark Mode (relaxed limits/bounds for BIRD EX scoring) vs. Enterprise Guard Mode (strict allowlists, row limits, date bounds, and `EXPLAIN` cost gates).
- **Repairer & Reflector (8b):** max 2 attempts, same schema never widened, order of B9 (`src/sql/repairer.py`), empty-result handling with execution error reflection scoring.
- **Executor (9):** read-only multi-datasource pool (`src/gateway/` and `src/sql/executor.py`, dialect-agnostic via connection gateway), timeout, result-size cap.
- **Reconciler (10):** grain, nulls, duplicates, registry comparison (`src/sql/reconciler.py`); one retry then caveat.

**Evaluation harness (design D)**
- Runner (`eval/eval_harness.py`), Baseline A (naive: one prompt, flattened schema, first output) and Baseline B (our 4-stage NL2SQL engine with grounded retrieval, targeting $\ge 70\%$ EX on BIRD Mini-Dev), System C (Full Agent with Analysts and Metric Compiler), the ablation ladder, paired per-question reports with refusals and failures in the denominator, scale test across 50 / 200 / 500 tables, results committed to `eval/results/<tag>/`.
- The runner calls AN-1's scoring scripts and BE-2's leakage check; it feeds the dashboard.

**Done when:** all hard gates (Section 6) are zero on the development set; each step returns its design-B3 contract; every failure mode in design B14 has a test; the harness produces a full report for a tag with one command.

### 4.4 BE-2: Backend B, Knowledge Onboarding and Trust

#### 4.4.1 Warehouse Onboarding Pipeline (first connection and refresh)

Triggered when a warehouse is connected for the first time (and later on a schedule or on demand). It turns a raw read-only warehouse connection into everything the query-time pipeline needs. **It never writes to the warehouse.**

| # | Stage | What it does | Output |
|---|---|---|---|
| 0 | `connect` | Verify the connection, confirm it is read-only (warn loudly if the credentials could write), detect the SQL dialect | Connection record (secrets kept in environment config, never in the repo or logs) |
| 1 | `introspect` | Read DDL, declared constraints, row counts, last-updated times for every table | Raw structure |
| 2 | `profile` | Per column: type, null rate, distinct count, min/max, formats, sample values. **Sample large tables** under a cost budget rather than full-scanning | Column profiles |
| 3 | `mask` | Detect sensitive columns by rules, let the user confirm or add via the review screen, exclude or mask them in profiles, samples, prompts, traces and logs | Masked profiles, sensitive-column list |
| 4 | `trust_features` | Freshness, row count, duplicate rate, historical query usage when logs exist, "likely stale copy" flag | Table trust features |
| 5 | `join_discovery` | Use declared foreign keys; where keys are missing, the join classifier proposes edges from name similarity, value overlap and type match; **verify empirically** (key uniqueness, containment); record cardinality | Join edges with status `proposed` / `verified` / `approved` / `rejected` |
| 6 | `descriptions` | Keep existing descriptions. Missing descriptions may be **model-drafted and marked "draft"** until a person reviews them. *(Proposed addition beyond the design; the Lead confirms.)* | Descriptions with draft flag |
| 7 | `index_build` | Build the **keyword index**, the **dense index in the vector DB** (tables, columns, descriptions, golden queries), and the **Value Index** (categorical and low/medium-cardinality values, n-gram lookup for high-cardinality names) | Search indexes |
| 8 | `knowledge_graph` | Assemble the graph (below) | Knowledge graph |
| 9 | `self_test` | Check that the build is usable: every table is retrievable from its own name and description, join paths exist for sample table pairs, no sensitive value leaked into an index | Self-test result |
| 10 | `report` | Summarise counts, masked columns, unverified joins, stale-copy flags, warnings | Onboarding Report |

**Knowledge graph (proposed definition; the Lead confirms).** The design's Join Graph is one part of it. Nodes: tables, columns, governed metrics, business terms and synonyms. Edges: joins (with cardinality and status), column-belongs-to-table, metric-uses-table-and-column, term-synonym-of, stale-copy-of. The Validator and Retriever use **approved join edges only**; the rest of the graph supports retrieval, answerability and refusal reasons.

**Vector DB choice.** The store (embedded such as FAISS, Chroma or LanceDB, versus a server) is decided by BE-2 with the Lead in week 1 and recorded in `contracts/knowledge_api.md`. The embedding model and reranker versions are recorded with each build.

**Pipeline behaviour**
- **Idempotent and resumable:** re-running a stage does not duplicate data; a failed stage resumes from its last good point.
- **Versioned:** every complete build gets a `catalog_version`. BE-1's cache and Golden Store entries are tied to it.
- **Incremental refresh:** on schedule, detect changed tables (structure or freshness), re-profile and re-index only those, and report what changed.
- **Human review gate:** the job ends in `needs_review` while proposed join edges or sensitive-column suggestions are waiting for approval. Only approved edges are used for query generation.
- **Progress:** each stage reports status and counts through the onboarding job record (5.6), which FE-1 displays.
- **Scale:** tested on the 50 / 200 / 500-table variants; the report states build time and cost per size.

#### 4.4.2 Knowledge stores and workflows

- **Metadata Catalog**, **Join Graph and knowledge graph**, **search indexes and Value Index**: read interfaces in 5.7 for BE-1.
- **Metric Registry:** schema, storage, seed metrics (name, synonyms, formula, required filters, grain, date column, allowed dimensions, base table and join path with cardinalities, caveats, reconciliation values), and the change-approval workflow. Optional: suggest candidate metrics from column names; **never auto-approved**.
- **Golden Store:** storage and promotion workflow. A query is promoted only after result checks pass **and** a person approves. Only development-set material goes in; **leakage check** against held-out questions before every final run.
- **Trace Store** and cache storage.
- **Action Library** storage (content by AN-1).

#### 4.4.3 Trust layer

- **Evidence Ledger:** numbered claims, each linked to a query ID or module artifact with an evidence grade; unsupported claims dropped or labelled hypothesis.
- **Synthesizer:** writes from the ledger only (the model may paraphrase, never add a figure); template-based fallback.
- **Fact Checker:** every number in the final text must exist in the ledger; "caused", "because of", "due to" are blocked unless a grade 4 label and its limits are attached; causal-boundary note (E5) is appended to every driver analysis.

#### 4.4.4 Warehouse generator and deployment

- **Synthetic warehouse generator** in `data_gen/` with seeds and config: scale ladder (about 50 / 200 / 500 tables), cryptic naming, stale and duplicate entities, messy values, merged customers, missing declared keys, soft deletes, late and truncated data, distractor tables, unanswerable asks. It exposes a **plug-in hook** where AN-1's planted truth is injected and the manifest is written.
- **Two snapshots** with different seeds and injection configs: build and held-out.
- Public dataset intake with the provenance table (name, URL, version, licence, permitted use, split).
- **Deployment:** public web app, secrets in environment config, model-hosting decision made early (design F2).

**Done when:** pointing the pipeline at a fresh warehouse (generated, 50 tables first) produces a catalog, join graph, indexes, knowledge graph and onboarding report with one command, and rebuilds from scratch; the ledger and Fact Checker reject planted unsupported claims and planted causal overclaims; refresh detects a changed table and only rebuilds that table.

---

## 5. Interface contracts

> **Source of truth:** machine-readable schemas live in `contracts/` (JSON Schema / OpenAPI). The shapes below are the agreed starting point; the files in `contracts/` supersede them once committed. Names are final unless changed through 7.4.
>
> **Full input/output formats for every hand-off** (request bodies, run-event payloads, error format, each pipeline step's input and output, analyst inputs, knowledge-store record schemas and Python signatures, ledger / synthesizer / fact-checker, ground-truth manifest, eval formats, and the week-1 fixture list) are in the companion file **`Interfaces.md`**. This section gives the headline shapes; `Interfaces.md` is the detailed reference.

### 5.1 API surface (BE-1 implements all routes; FE-1 consumes)

| Method and path | Purpose | Backing service |
|---|---|---|
| `POST /api/ask` | Start a run. Body `{ question, session_id, clarification_of? }`, returns `{ run_id }` | BE-1 |
| `GET /api/runs/{run_id}/events` | Server-sent events: `step_started`, `step_finished`, `clarify`, `answer`, `error` | BE-1 |
| `GET /api/runs/{run_id}` | Final `AnswerEnvelope` | BE-1 |
| `GET /api/runs/{run_id}/trace` | Full trace | BE-1 / BE-2 (Trace Store) |
| `GET /api/runs?limit=` | Recent runs | BE-1 |
| `GET /api/artifacts/{artifact_id}` | Chart data and table artifacts for evidence views (`Interfaces.md` 11.1) | BE-1 |
| `POST /api/runs/{run_id}/actions/{action_id}/decision` | Record the person's approve / reject. Body `{ decision, note? }` | BE-1 |
| `POST /api/runs/{run_id}/review-request` | Request analyst review of a low-confidence answer | BE-1 |
| `POST /api/warehouse/connect` | Start onboarding. Returns `{ job_id }` | BE-2 service |
| `GET /api/onboarding/{job_id}` | `OnboardingJob` status (5.6) | BE-2 service |
| `GET /api/onboarding/{job_id}/report` | `OnboardingReport` | BE-2 service |
| `GET /api/joins?status=` and `POST /api/joins/{edge_id}/decision` | Join-edge review | BE-2 service |
| `GET /api/columns/sensitive` and `POST /api/columns/sensitive` | Sensitive-column review | BE-2 service |
| `GET /api/golden/candidates` and `POST /api/golden/candidates/{id}/decision` | Golden Store review | BE-2 service |
| `GET /api/registry/changes` and `POST /api/registry/changes/{id}/decision` | Metric Registry review | BE-2 service |
| `GET /api/metrics?tag=&snapshot=` and `GET /api/eval/results/{tag}` | Dashboard and eval results | BE-1 harness |

BE-1 owns the API framework, auth stub and routing. BE-2 provides the service functions behind the rows marked "BE-2 service" (Python interfaces in 5.7); BE-1 wires them as thin routes.

### 5.2 `AnswerEnvelope` (BE-1 assembles; BE-2 supplies ledger and text; AN-1 supplies module outputs)

```ts
type RunStatus =
  | "answered" | "clarify" | "refused" | "blocked"
  | "insufficient_evidence" | "error";

interface AnswerEnvelope {
  run_id: string;
  status: RunStatus;

  route: { labels: string[]; confidence: number; reason: string };   // shown on every answer
  conclusion: string | null;                       // only what evidence supports

  scope: {
    metric: string | null; window: string | null; filters: string[];
    grain: string | null; data_range: string | null;
    governed: boolean;                             // false => show "ungoverned metric"
  };

  evidence: EvidenceBlock[];                       // wraps AnalystOutput results (5.3)
  claims: LedgerClaim[];                           // 5.4: every number links here
  method: { name: string; params: Record<string, unknown> }[];
  queries: QueryRecord[];                          // executed SQL, status, timing
  caveats: string[];
  causal_boundary_note: string | null;             // required when a driver analysis ran
  next_actions: ActionOption[];                    // 2-3 ranked, never auto-executed

  clarification?: { what_understood: string; what_needs_clarification: string };
  refusal?: { missing: string[]; closest_answerable?: string };
  block?: { reason: "write_attempt" | "injection" | "unsafe"; detail: string };

  run_metrics: { latency_ms: number; tokens: number; queries: number; rows: number; retries: number };
  provenance: { model_name: string; model_version: string; commit: string;
                snapshot_id: string; catalog_version: string; timestamp: string };
}

interface EvidenceBlock { module: "lookup" | "anomaly" | "driver" | "risk"; output: unknown; source_claims: string[] }

interface QueryRecord {
  query_id: string; sql: string; status: "ok" | "rejected" | "error";
  duration_ms: number; rows_scanned?: number; est_cost?: number;
  origin: "compiler" | "generator" | "repair"; repair_of?: string;
  validation_mode?: "benchmark" | "enterprise";
}

interface ActionOption {
  action_id: string; title: string; rank: number;
  assumptions: string[]; trade_offs: string[];
  sensitivity: string; monitor: string[]; data_that_would_change_ranking: string[];
  kind: "intervention" | "information_gathering";
}
```

### 5.3 Analyst contract (AN-1 provides; BE-1 calls; FE-1 renders via `evidence`)

```ts
interface AnalystOutput<T> {
  module: "anomaly" | "driver" | "risk";
  status: "ok" | "insufficient_evidence";
  reason?: string;                          // required when insufficient_evidence
  result?: T;
  parameters: Record<string, unknown>;
  assumptions: string[];
  sources: string[];                        // query_ids the module consumed
  artifacts: { artifact_id: string; kind: "chart_data" | "table" | "model_card"; uri: string }[];
}

interface AnomalyResult {
  verdict: "unusual" | "within_normal_range" | "insufficient_evidence";
  window: string; actual: number; expected: number;
  expected_range: [number, number]; deviation_pct: number; robust_score: number;
  persistence: "one_off" | "sustained" | "drift" | "unknown";
  threshold: number; correction_used: string | null;
  calendar_effects: string[]; baseline_kind: "seasonal" | "robust_trailing";
}

interface DriverResult {
  metric: string; current_period: string; comparison_period: string; total_change: number;
  drivers: {
    dimension: string; value: string; contribution: number; share: number;
    interval: [number, number]; sample_size: number; unstable: boolean;
    grade: 0 | 1 | 2 | 3 | 4;
    data_needed_to_strengthen: string[];
  }[];
  reconciliation_error: number;             // must be ~0
  candidates_examined: number; correction_used: string | null;
}

interface RiskResult {
  churn_definition: { window_days: number; active_definition: string; cutoff: string };
  one_time_buyer_share: number;
  model_used: "heuristic" | "logistic" | "gradient_boosted";
  metrics: { pr_auc: number; prevalence: number; roc_auc: number; brier: number; lift_at_k: number; k: number };
  segments: { segment: string; mean_risk: number; revenue_at_risk: number; interval: [number, number] }[];
  risk_drivers: { feature: string; importance: number }[];   // association, not cause
  limits: string[];
}
```

### 5.4 Evidence Ledger claim (BE-2 provides; FE-1 renders; Fact Checker enforces)

```ts
interface LedgerClaim {
  claim_id: string;                          // "C1", "C2", ...
  text: string;                              // sentence as shown
  numbers: { token: string; value: number; unit?: string }[];
  source: { type: "query" | "artifact"; id: string };
  grade: 0 | 1 | 2 | 3 | 4;                  // design B11
  kind: "fact" | "concentration" | "co_movement" | "comparison" | "consistent_with" | "hypothesis";
}
```

### 5.5 Trace record and events (BE-1 emits; BE-2 stores; FE-1 renders)

```ts
interface TraceEvent {
  run_id: string; step: number;               // 1..14, 8b for Repairer, 11b for Advisor
  component: string;                          // e.g. "Validator"
  impl: "code" | "model" | "mixed";
  status: "ok" | "failed" | "skipped" | "retried";
  started_at: string; duration_ms: number;
  input_summary: string; output_summary: string;
  detail?: Record<string, unknown>;           // retrieved schema, join path, validator checks, repair diff, reconciliation
  tokens?: { in: number; out: number };
}
```

Sensitive columns are masked **before** a trace event is written. The frontend never receives raw sensitive values.

### 5.6 Onboarding contracts (BE-2 provides; FE-1 renders)

```ts
type OnboardingStage =
  | "connect" | "introspect" | "profile" | "mask" | "trust_features"
  | "join_discovery" | "descriptions" | "index_build" | "knowledge_graph"
  | "self_test" | "report";

interface OnboardingJob {
  job_id: string;
  mode: "initial" | "refresh";
  status: "queued" | "running" | "needs_review" | "completed" | "failed";
  stages: {
    stage: OnboardingStage;
    status: "pending" | "running" | "done" | "warning" | "failed";
    started_at?: string; duration_ms?: number;
    counts?: Record<string, number>;          // e.g. tables, columns, edges
    message?: string;
  }[];
  catalog_version: string | null;             // set when the build completes
  pending_review: { joins: number; sensitive_columns: number; descriptions: number };
}

interface JoinEdge {
  edge_id: string;
  from: { table: string; columns: string[] };
  to:   { table: string; columns: string[] };
  cardinality: "1:1" | "1:N" | "N:1" | "N:M";
  source: "declared" | "classifier";
  evidence: { name_similarity?: number; value_overlap?: number; type_match?: boolean;
              key_uniqueness?: number; containment?: number };
  status: "proposed" | "verified" | "approved" | "rejected";   // declared keys start approved
}

interface OnboardingReport {
  catalog_version: string; built_at: string; build_seconds: number;
  counts: { tables: number; columns: number };
  masked_columns: number;
  joins: { declared: number; proposed: number; verified: number; approved: number; rejected: number };
  stale_copy_flags: string[];
  draft_descriptions: number;
  warnings: string[];
  self_test: { passed: boolean; failures: string[] };
  embedding_model: string; reranker_model: string;
}
```

### 5.7 Knowledge-store read interfaces (BE-2 provides; BE-1 consumes)

| Interface | Returns |
|---|---|
| `catalog.get_tables(ids)` / `catalog.get_columns(table)` | DDL, descriptions, column profile, trust features |
| `joingraph.paths(tables, approved_only=True)` | Join paths with cardinalities and approval status |
| `kgraph.neighbors(node, edge_types)` | Related tables, columns, metrics and terms |
| `registry.resolve(term)` / `registry.get(metric_id)` | Governed definition or `None`, plus reconciliation value |
| `index.search(query, kind)` | Ranked candidates with scores (hybrid, from keyword and vector indexes) |
| `values.link(phrase, column_hint?)` | Stored literal with confidence |
| `golden.nearest(question, k)` | Verified exemplar question-SQL pairs |
| `actions.match(driver_type)` | Action Library entries |
| `ledger.build(analyst_outputs, queries)` / `factcheck.verify(text, ledger)` | Ledger / pass-fail with offending sentences |
| `onboarding.start(connection)` / `onboarding.status(job_id)` / `onboarding.refresh()` | Job control for the routes in 5.1 |

These are Python interfaces; signatures go in `contracts/knowledge_api.md`.

---

## 6. Hard gates and who guards them

These must be **exactly zero** on every evaluation run (design A4, D4). A regression is a release blocker.

| Gate | Primary guard | Verified by |
|---|---|---|
| Executed query references a table or column not in the retrieved allowlist | BE-1 (Validator) | BE-2 (injects unknown-identifier cases into eval and reviews Validator tests) |
| Executed write, DDL or file-access statement | BE-1 (Validator and read-only Executor) | BE-2 (confirms read-only credentials at onboarding and reviews Executor tests) |
| Numeric claim with no link to a query result or module output | BE-2 (Ledger and Fact Checker) | FE-1 (UI flags any unlinked number), BE-1 (eval) |
| Answer exceeds its evidence grade (causal overclaim) | BE-2 (Fact Checker) | AN-1 (grades are correct), FE-1 (badges) |
| Sensitive value appears in a prompt, index, trace or log | BE-2 (masking at onboarding) | BE-1 (trace emission), FE-1 (never renders raw) |
| Unapproved join edge used in generated SQL | BE-1 (Validator) | BE-2 (edge status in graph) |

---

## 7. Working agreements

### 7.1 Repository and branching

```
web/                          FE-1
src/api/ src/orchestrator/ src/pipeline/ eval/         BE-1
src/onboarding/ src/knowledge/ src/ledger/ src/factcheck/ src/synth/
data_gen/ deploy/ observability/                       BE-2
src/analysts/ src/advisor/ data_gen/plants/            AN-1
prompts/<component>/          owner of that component
contracts/                    shared (see 7.4)
agent_setup/                  Lead
```

- A `CODEOWNERS` file mirrors Section 3.
- Branches: `feat/<id>-<topic>`, for example `feat/an1-driver-bootstrap`. No direct pushes to `main`.
- Every PR needs one review from the backup reviewer in Section 3. PRs touching the Validator, Executor, Fact Checker or `contracts/` also need the Lead. When the PR author is the Lead (Ganesh), the second reviewer is BE-2 (Vishal) instead, so nobody approves their own change.
- Releases are tagged `v0.1` to `v1.0` by the Lead only, after the evaluation suite has run and results are committed to `eval/results/<tag>/`.

### 7.2 Stubs first, so nobody waits

| Week 1 deliverable | Owner | Unblocks |
|---|---|---|
| Fixture-replay API server serving an `AnswerEnvelope`, trace and event stream for each `status` | BE-1 | FE-1 |
| Mock onboarding job and report fixtures (running, needs_review, failed, completed) | BE-2 | FE-1 |
| **Hand-built stub catalog and join graph** for a small first warehouse (a few tables) | BE-2 | BE-1 (retrieval, Validator) |
| Example `AnalystOutput` JSON for every verdict including `insufficient_evidence` | AN-1 | FE-1, BE-1 |
| Ground-truth manifest format and a small sample manifest | AN-1 and BE-2 together | AN-1 scoring, BE-2 generator |
| Example `LedgerClaim` sets (one with a grade 4 claim, one with a hypothesis) | BE-2 | FE-1, BE-1 |

FE-1 builds only against fixtures until the real API passes contract tests. Nobody changes a fixture shape without 7.4.

### 7.3 Communication and cadence

- Daily async update (3 lines: done, next, blocked).
- Twice-weekly 30-minute sync for contract partners (Section 2).
- Weekly integration demo: every member shows their part running in the integrated build.
- Blocked more than one working day: raise to the Lead.
- A contract question from a partner gets an answer within one working day.

### 7.4 Changing a contract

1. Open a PR editing `contracts/` (schema and example) with a one-paragraph reason.
2. The producer and every consumer named in Section 5 approve; the Lead merges.
3. Breaking changes bump the contract version; the producer keeps the old shape working for one prototype tag unless all consumers agree otherwise.
4. Producer-side and consumer-side contract tests pass in CI before merge.

### 7.5 Definition of done (every deliverable)

- Matches its contract and passes contract tests.
- Unit tests for normal and edge cases (empty, sparse, short history, zero baseline, model timeout).
- Prompts used are in `prompts/`, versioned.
- Failure behaviour matches design B14 (fail closed, say exactly what is missing).
- No secrets and no raw sensitive values in code, fixtures, logs or screenshots.
- A component README explains how to run it.
- Reviewed by the named backup reviewer.

### 7.6 Reporting rule (everyone)

Targets are goals, not results. Any number shown in a UI, README, demo or report must be **measured**, with model name and version, commit hash, snapshot ID and date (design D6).

---

## 8. Milestones by prototype tag

Work is ordered by tag; there are no calendar dates in this contract. Each tag includes the evaluation run for that tag.

| Tag | Goal (design F1) | FE-1 | AN-1 | BE-1 | BE-2 |
|---|---|---|---|---|---|
| **v0.1** | End-to-end slice and naive baseline | Scaffold, design system, API client, Ask box, streaming progress, basic answer page (conclusion, scope, SQL), basic trace list | Module contract, example outputs, test harness, manifest format agreed | API, Orchestrator skeleton, mock server, Guard, retrieval v1, Generator, Validator v1, Executor, eval runner v1, **Baseline A** | Onboarding pipeline v1 (connect, introspect, profile, mask, keyword and dense index, catalog) for the first warehouse; generator v1; build snapshot v1; trace store |
| **v0.2** | Hybrid retrieval, join graph, value linking, repair, refusal | Connect and onboarding progress and report screens, join-edge and sensitive-column review, clarification, refusal and blocked screens, retrieved-schema and join-path view | Anomaly module v1 (baseline and scoring), first planted anomalies | Hybrid retrieval, join expansion, value linking, Repairer, Answerability Checker, refusal, **Baseline B**, scale ladder 50 / 200 | Join discovery and classifier with empirical verification, Value Index, knowledge graph, self-test, onboarding report, human-review gate, refresh v1 |
| **v0.3** | Metric Registry, SQL Compiler fast path, Reconciler, cost gate | "Ungoverned metric" label, reconciliation caveat, validator and reconciliation panels | Anomaly: change-point, FDR control, volume guard | Metric Resolver, Compiler, Reconciler, cost gate, slot-based cache, Router and Planner v1 | Metric Registry and change workflow, table trust features, Golden Store storage, 500-table variant, incremental refresh |
| **v0.4** | Anomaly and root-cause modules scored vs. planted truth | Plan progress for multi-step questions, anomaly chart with interval, driver bars | Anomaly final, **Driver Analyst**, planted drivers, confounders and negative controls, scoring scripts | Planner validated against Analyst registry, Analyst calling and typed IO, module scores in eval | Generator plug-in hook used by AN-1, manifest handling, held-out snapshot |
| **v0.5** | Risk Analyst, Advisor, evidence grading | Risk segment table, grade badges, recommendations with approve / reject | **Risk Analyst**, **Advisor**, Action Library content, churn process | Decision endpoint, Advisor integration | Action Library storage, grade fields in ledger schema |
| **v0.6** | Evidence Ledger, Fact Checker, trace UI, dashboard | Ledger-linked numbers, causal-boundary note, unsupported-number flag, full trace view, metrics dashboard, pass-rule scoreboard, Golden and Registry review screens | Fixes from module evaluation, sensitivity analysis | Ledger and Fact Checker integrated into the orchestrator, model-fallback behaviour, observability feed | **Ledger, Synthesizer, Fact Checker**, template fallback, golden promotion workflow |
| **v1.0** | Held-out evaluation, scale ladder, public deployment | Polish, error states, responsive layout; P2 items only if time allows | Held-out module results | Held-out run, ablation and scale reports, hardening, budgets | Leakage check, deployment, provenance table, final report inputs |

---

## 9. Decisions confirmed by the Lead before kickoff

- [x] **Knowledge Graph & Descriptions (4.4.1):** Approved. Knowledge graph tracks table-column relations; descriptions use existing catalog metadata, with missing descriptions model-drafted and explicitly flagged `"draft"` until verified.
- [x] **Vector DB choice:** `Chroma` (or local `FAISS`) with `all-MiniLM-L6-v2` embeddings for fast, zero-cost, deterministic local embedding retrieval.
- [x] **Frontend Stack:** React + TypeScript with modern component design and Vite bundler (`apps/web/`). Backend is Python 3.11 with FastAPI (`src/app.py`).
- [x] **Deployment Choice:** Hosted LLM API (OpenAI-compatible endpoint for DeepSeek/Qwen/GPT-4o) with strict token and latency budgets (`OPS-2`).
- [x] **Target SQL Dialects:** SQLite for BIRD benchmark scoring and local testing; MySQL and PostgreSQL for enterprise warehouse demonstration.
- [x] **Public Datasets & Licences:** BIRD Benchmark (academic use) + synthetic enterprise schema (open-source MIT/CC-BY-4.0); provenance table maintained by BE-2.
- [x] **Threshold Calibration (D4):** Development set calibration during `v0.1`–`v0.4`; frozen values committed to `spec/thresholds.md` before `v1.0` held-out run (`EVAL-7`).
- [x] **Frontend Tiers (4.1):** P0 / P1 / P2 milestone staging confirmed with FE-1 (Abdul).

---

## 10. Risks and cross-dependencies (watch list)

| Risk | Why | Mitigation |
|---|---|---|
| **FE-1 is a single point of capacity** | One person covers every screen | P0 / P1 / P2 tiers in 4.1; Lead decides cuts at each tag; fixtures and contract-generated client avoid rework; a backend member may take a named read-only screen only by written handoff recorded in `CODEOWNERS` |
| **BE-1 waits on BE-2's knowledge stores** | Retrieval and Validator need catalog, join graph, indexes | BE-2 ships the stub catalog and join graph in week 1 (7.2) and onboarding v1 by `v0.1` |
| **BE-2 carries the broadest scope** | Onboarding, stores, trust layer, generator, deployment | Order of work follows the milestone table; generator and deployment items can slip to later tags; Lead reviews load at `v0.2` |
| **AN-1 needs data with planted truth** | The generator is BE-2's, the plants are AN-1's | Plug-in hook and manifest format agreed in week 1; small manifest shipped first |
| **Ledger and Fact Checker boundary** | BE-1 calls them, BE-2 owns them | BE-1 only calls `ledger.build` and `factcheck.verify` (5.7); BE-2 owns internals |
| **Held-out leakage** | Authors of dev questions could also tune on held-out | Lead controls the held-out set; BE-2's leakage check runs before every final run |
| **Join approval becomes a bottleneck** | Many proposed edges at 500 tables | Declared keys start approved; verified classifier edges are batched in the review screen; only approved edges are used |
| **Optimistic diagnostic latency (4.5s)** | Bootstrap resampling (1,000 runs) + LLM narrative | SLA breach on analytical routes | Profile on dev set during `v0.4`; optimize numpy resampling; calibrate SLA and freeze budget in `spec/thresholds.md` per `OPS-6` and `EVAL-7` |

---

## 11. Sign-off

| Role | Name | Signature |
|---|---|---|
| Team Lead and BE-1 | Ganesh | |
| FE-1 | Abdul | |
| AN-1 | Swathi | |
| BE-2 | Vishal | |

# Requirements Specification — Data Scientist Agent
 
**Location in repo:** `spec/requirements.md`  
**Sources:** `DESIGN_DETAIL.md` (Design, sections A–F), PRD v1.0 (PPR-01 to PPR-06, sections 5–8), and the capstone brief.  
**Rule for every prompt, test and commit:** cite the requirement ID(s) it implements or verifies.
 
## 0. Conventions
 
- **Priority:** P0 = needed for a hard gate or the pass rule; P1 = needed for the full design; P2 = designed, may be deferred and must then be listed under "designed but not built" in `ARTEFACT.md`.
- **Verification:** UT = unit test, IT = integration test, EV = evaluation metric on the eval set, INS = inspection or architecture test, OBS = observed on the running system.
- **Owner roles:** FE-1 (frontend), AN-1 (analysis and recommendation), BE-1 (query-time pipeline), BE-2 (knowledge onboarding, data and trust).
- **Targets are goals, not results.** A number is reported only after the evaluation has run, with model name and version, commit hash, snapshot id and date (Design D6).
- **Wording:** MUST is mandatory; SHOULD is expected unless a documented trade-off says otherwise.

---
 
## 1. Reconciliation of PRD and Design (decisions to confirm)
 
The PRD and the Design disagree in places. The Design is used where they conflict because it covers the brief's mandatory scenarios (anomaly, root cause, churn risk) and the planted-truth evaluation. **Please confirm or override each row; the requirements below follow the "Proposed resolution" column.**
 
| # | Topic | PRD says | Design says | Proposed resolution |
|---|---|---|---|---|
| R1 | Database engine | Single fixed engine | Dialect-agnostic SQL Gateway | The system is engine-neutral: validator and executor take a configured dialect setting. No engine-specific lock-in; connects to any SQL-compliant database or warehouse (RTE-1, SQL-1). |
| R2 | Cache | Semantic cache on question-embedding similarity | Slot-based cache, because similar questions (for example two regions) need different SQL | Slot-based plan cache plus exact-match cache. Embedding-similarity cache is rejected. |
| R3 | Analysis scope | Phases 3–4 (later) | Anomaly, Driver, Risk and Advisor are core (brief scenarios) | Analysts are in scope for the first release. |
| R4 | Anomaly method | Z-score or IQR | Seasonal baseline with intervals, change-point and FDR control | Seasonal method is the module. Z-score/IQR (±2σ) is kept only as the module baseline. |
| R5 | Intents and routes | Descriptive, Comparative, Diagnostic, Prescriptive | Lookup, anomaly, root cause, risk, recommendation, ambiguous, unanswerable, unsafe | Keep both: the PRD intent label is shown to the user; the route decides execution (RTE-5). |
| R6 | Optimisation (OR-Tools) | In scope (Phase 4) | Deferred | Deferred to P2. |
| R7 | Thresholds | Fixed numbers | Calibrate on dev set, freeze before held-out run | PRD numbers are initial targets; the frozen values live in `spec/thresholds.md` (EVAL-7). |
| R8 | Latency | Avg ≤ 2.5 s, P95 ≤ 5 s for everything | Per-route budgets | Governed and lookup routes target the PRD SLA. Analysis routes (bootstrap, model scoring) get their own budget set from measurement (OPS-6). |
| R9 | Metric definitions | Not present | Metric Registry, Resolver, Compiler, Reconciler | Added (MET group). |
| R10 | Causal language | Tripartite finding / interpretation / recommendation | Evidence grades 0–4 and blocked causal words | Both apply (EVID-1, EVID-6). |
| R11 | Abstention message | Fixed template naming available domains | Refusal must name the missing attribute | Merge: template plus the missing attribute plus nearest answerable question (ABS-1). |
| R12 | Retrieval threshold | Confidence < 0.65 triggers re-query or abstain | Score threshold tuned for recall | 0.65 is the starting value, tuned on the dev set (GRD-10). |
| R13 | Cost gate | Specific engine metric | Engine-neutral cost estimate | Threshold is a per-engine/warehouse configuration value (SQL-8). |
| R14 | PRD internal conflicts | Schema validity ≥ 92% (PPR-05.1) vs ≥ 98% (section 7); execution accuracy ≥ 88% (PPR-05.1) vs first-pass ≥ 85% and result-set ≥ 88% (section 7); avg latency 2.5 s vs P50 2.0 s; cache hit 350 ms vs 300 ms | — | Use the stricter section 7 values as initial targets. |
| R15 | PRD Phase 1 "done" ticks | Marked complete | Prototype tags carry the evidence | Treat Phase 1 items as claims until a tagged version and committed eval results prove them. `ARTEFACT.md` reports only what is tagged and evaluated. |
| R16 | NL2SQL Engine & Dual-Mode Validation | Flat naive prompt | Native 4-stage NL2SQL pipeline + Metric Compiler | Dual-Engine: Metric Registry/Compiler for governed KPIs; native Perceptor/Generator/Reflector for exploratory queries; Dual-mode Validator (Benchmark mode for BIRD EX vs. Enterprise mode for strict warehouse safety). |
 
---
 
## 2. Safety and hard gates (all must be exactly zero or satisfied)
 
| ID | Requirement | Source | Verification | Pri | Owner |
|---|---|---|---|---|---|
| SAFE-1 | No executed query MUST reference a table or column outside the retrieved allowlist. | A4.1; PPR-06.1, 06.2 | EV (count = 0), UT adversarial set | P0 | BE-1 |
| SAFE-2 | No executed statement MAY be a write, DDL or file-access statement; the connection MUST be read-only (`SELECT` grant only). | A4.2; PPR-06.6; B16 | UT, IT, INS | P0 | BE-1 |
| SAFE-3 | Every numeric claim in a final answer MUST link to a query result or module output. | A4.3; PPR-06.10 | EV (unsupported-claim count = 0) | P0 | BE-1 |
| SAFE-4 | Text from the question or database MUST be treated as data, never as instructions; injection cases are blocked or ignored, and logged. | B14, B16, E6 | EV (injection set) | P0 | BE-1 |
| SAFE-5 | Sensitive columns MUST be masked or excluded from profiles, prompts, traces and logs. | B16; C5; E6 | UT, INS | P0 | BE-2 |
| SAFE-6 | The system MUST fail closed: when an attribute, definition or enough history is missing, it states exactly what is missing and does not guess. | E1; PPR-06.7 | EV (refusal set) | P0 | BE-1 |
| SAFE-7 | No secret or API key MAY appear in the repository, prompts, traces or logs. | Brief; F2 | INS (secret scan hook) | P0 | all |
| SAFE-8 | A model MUST NOT compute a number or decide whether a query is safe. Analyst, Validator and Reconciler packages MUST NOT import a model client. | B4; PRD 1.2 | INS (architecture test) | P0 | BE-1, AN-1 |
 
---
 
## 3. Guard, routing and planning
 
| ID | Requirement | Source | Verification | Pri | Owner |
|---|---|---|---|---|---|
| RTE-1 | A Guard MUST check every question for write attempts and injection before routing, and return a block reason. | B2, B3 | UT, EV | P0 | BE-1 |
| RTE-2 | The Router MUST combine a trained classifier, deterministic rules and a model proposal; code takes the final decision and disagreement lowers confidence. | B5 | UT, EV | P0 | BE-1 |
| RTE-3 | Below the confidence threshold the system MUST ask a clarifying question stating what was understood and what is needed. | B5; A2 | EV (clarification set) | P0 | BE-1 |
| RTE-4 | Every answer MUST show the route and the reason for it. | B5; Brief | IT, OBS | P0 | BE-1, FE-1 |
| RTE-5 | The system MUST assign one PRD intent label (Descriptive, Comparative, Diagnostic, Prescriptive) and map it to a route. | PPR-02.6; R5 | UT | P1 | BE-1 |
| RTE-6 | The Planner MUST turn compound questions into an ordered plan of registered Analysts with a depth cap; code validates the plan and an invalid plan falls back to a single module or a clarification. | B5, B3 | UT | P0 | BE-1 |
| RTE-7 | Routing MUST be evaluated by accuracy and confusion matrix, including ambiguous and out-of-scope classes. | B5; D1 | EV | P0 | BE-1 |
 
---
 
## 4. Knowledge, schema grounding and retrieval
 
| ID | Requirement | Source | Verification | Pri | Owner |
|---|---|---|---|---|---|
| GRD-1 | The offline build MUST create a Metadata Catalog (DDL, constraints, descriptions) with a profile per column (type, null rate, distinct count, min/max, formats, masked samples). | B7; C6; PPR-02.2 | UT, INS | P0 | BE-2 |
| GRD-2 | The Join Graph MUST hold tables and join edges with cardinality and approval status; where foreign keys are not declared, proposed edges MUST be verified empirically (key uniqueness, containment). | B7; PPR-01.4 | UT, EV | P0 | BE-2 |
| GRD-3 | Table trust features (freshness, row count, duplicate rate, likely-stale-copy flag) MUST be computed so a maintained table is preferred over an old copy. | B7; PPR-01.3 | UT, EV | P1 | BE-2 |
| GRD-4 | Retrieval MUST fuse a keyword index and a dense index with reciprocal rank fusion (k = 60 initially) and rerank with a cross-encoder. | B7; PPR-02.3, 02.4 | EV (recall@k, MRR) | P0 | BE-2 |
| GRD-5 | Retrieval MUST be tuned for recall using a score threshold, then pruned; the full schema MUST NOT be placed in a prompt. Retrieved schema context SHOULD stay under 2,000 tokens. | B7; PPR-01.1, 05.2 | EV, OBS | P0 | BE-2 |
| GRD-6 | Join expansion MUST use approved edges only and find paths by shortest-path or Steiner-tree approximation. | B7; PPR-01.4 | UT, EV (join-path correctness) | P0 | BE-2 |
| GRD-7 | Column pruning MUST keep only relevant columns plus keys. | B7 | UT | P1 | BE-2 |
| GRD-8 | A Value Index MUST support exact and fuzzy/trigram lookup; value linking MUST return a confidence and low confidence MUST trigger a clarification. | B7; PPR-06.4 | EV (value-grounding accuracy) | P0 | BE-2 |
| GRD-9 | Profile-based column warnings (null share, mixed formats, soft-delete flags) MUST be passed to the SQL Generator. | B7 | UT | P1 | BE-2 |
| GRD-10 | If a needed table or attribute cannot be retrieved with adequate confidence (starting value 0.65), the system MUST clarify or refuse and MUST NOT ask the model to fill the gap. | B7; PPR-02.7 | EV | P0 | BE-1 |
| GRD-11 | Synonyms and business annotations MUST be supported so inconsistent names (`cust_id`, `client_id`) map to one concept. | PPR-01.2, 01.3, 02.1 | EV | P1 | BE-2 |
| GRD-12 | Every store (catalog, graph, indexes, registry) MUST be rebuildable from scripts, and the onboarding pipeline MUST run when a warehouse is first connected. | C5; F3 | IT | P0 | BE-2 |
 
---
 
## 5. Metric governance
 
| ID | Requirement | Source | Verification | Pri | Owner |
|---|---|---|---|---|---|
| MET-1 | The Metric Registry MUST record per metric: name and synonyms, formula, required filters, grain, date column, allowed dimensions, base table and join path with cardinality, caveats. | B6; C6 | INS, UT | P0 | BE-2 |
| MET-2 | The Metric Resolver MUST run on every question before SQL is produced. | B6 | IT | P0 | BE-1 |
| MET-3 | A fully governed question MUST use the SQL Compiler (no model call, same SQL each time). | B6; B15 | UT, EV (consistency) | P0 | BE-1 |
| MET-4 | A partly governed question MUST pass definitions to the Generator as hard constraints; an ungoverned metric MUST be labelled "ungoverned metric" in the answer. | B6 | IT | P1 | BE-1 |
| MET-5 | For governed metrics the Reconciler MUST compare results with the registry value within a stated tolerance; a mismatch triggers one retry, then a visible caveat. | B6; B8 | UT, EV | P1 | BE-1 |
| MET-6 | New or changed metric definitions MUST be approved by a person. | B13 | INS | P1 | BE-2 |
 
---
 
## 6. SQL generation, validation, execution and repair
 
| ID | Requirement | Source | Verification | Pri | Owner |
|---|---|---|---|---|---|
| SQL-1 | The Generator MUST receive only the retrieved schema, definitions and a few verified exemplars, and the target dialect rules. | B3; PPR-06; E2 | INS, IT | P0 | BE-1 |
| SQL-2 | The Validator MUST parse with an SQL parser in the target dialect and reject parse errors. | B8.1; PPR-06.5 | UT | P0 | BE-1 |
| SQL-3 | The Validator MUST require exactly one statement whose root is a read-only `SELECT` (read-only CTEs allowed). | B8.2 | UT | P0 | BE-1 |
| SQL-4 | The Validator MUST reject DML, DDL, stacked statements, file-reading and extension functions, and system-catalog access, including comment and quoting tricks. | B8.3; PPR-06.6 | UT (adversarial set) | P0 | BE-1 |
| SQL-5 | The Validator MUST check every table and column against the retrieved allowlist before execution. | B8.4; PPR-06.1, 06.2 | UT, EV | P0 | BE-1 |
| SQL-6 | The Validator MUST accept only joins on approved Join Graph edges and flag fan-out risk. | B8.5; PPR-06.3 | UT, EV (trap set) | P0 | BE-1 |
| SQL-7 | In enterprise mode, the Validator MUST require row limits and date bounds and reject unrestricted `SELECT *`. In benchmark mode, limits are relaxed to preserve BIRD gold result set comparisons. | B8.6; B5 | UT, EV | P1 | BE-1 |
| SQL-8 | A cost gate (`EXPLAIN` or dry run) MUST estimate scanned data and reject or rewrite above a configurable budget; pre-aggregated tables SHOULD be preferred. | B8.7; PPR-05.4; R13 | UT, IT | P1 | BE-1 |
| SQL-9 | The Executor MUST use a read-only connection with a timeout (10 s initially) and result-size cap. | B8.8; PPR-05.x; 5.8 | IT | P0 | BE-1 |
| SQL-10 | After execution the Reconciler MUST check expected columns, grain, duplicates, NULL behaviour and sane ranges. | B8.9; PPR-06.9 | UT, EV | P1 | BE-1 |
| SQL-11 | The Repairer MUST make at most 2 attempts using the error, failed SQL and the same schema; the schema MUST NOT be widened automatically. | B9; PPR-06.8 | UT, EV (repair success) | P0 | BE-1 |
| SQL-12 | An empty result MUST NOT be treated as automatically wrong; the checker MUST separate "wrong filter" from "true empty". | B9 | UT | P1 | BE-1 |
| SQL-13 | After the repair cap or a policy violation the system MUST return a structured clarification (`what_understood`, `what_needs_clarification`). | B9 | IT | P0 | BE-1 |
| SQL-14 | Candidate voting for hard questions SHOULD be implemented as an ablation, not by default. | B9 | EV | P2 | BE-1 |
| SQL-15 | If the model is unavailable, the system MUST fall back to the Compiler path where possible, otherwise return a clear error and never a guess. | B14 | IT | P1 | BE-1 |
| SQL-16 | The system MUST support a native 4-stage NL2SQL pipeline (`src/schema_retriever/perceptor.py`, `src/sql/generator.py`, `src/sql/validator.py`, `src/sql/repairer.py`) with ICL exemplar routing for ad-hoc and exploratory queries. | B4; F1 | UT, EV | P0 | BE-1 |
| SQL-17 | The Validator MUST support a `BENCHMARK_MODE` toggle: in benchmark mode, row limits and mandatory date bounds are relaxed to allow BIRD execution accuracy (EX) scoring without altering result sets. | B5; D1 | UT, EV | P0 | BE-1 |
 
---
 
## 7. Answerability and abstention
 
| ID | Requirement | Source | Verification | Pri | Owner |
|---|---|---|---|---|---|
| ABS-1 | A question that the catalog cannot answer MUST produce a refusal that names the missing attribute, lists the domains the warehouse does cover, and suggests the closest answerable question. | A2; B14; PPR-06.7; R11 | EV (refusal set) | P0 | BE-1 |
| ABS-2 | The Answerability Checker MAY use keyword rules only as a first filter; it MUST be backed by retrieval confidence and a labelled refusal set with paraphrases. | E3 | EV (precision and recall) | P0 | BE-1 |
| ABS-3 | Near-miss answerable questions MUST NOT be over-refused, and the over-refusal rate MUST be reported. | D1; D2 | EV | P0 | BE-1 |
| ABS-4 | Ambiguous questions MUST trigger a clarification instead of a guess. | A2; B5 | EV | P1 | BE-1 |
 
---
 
## 8. Analyst registry (shared contract)
 
| ID | Requirement | Source | Verification | Pri | Owner |
|---|---|---|---|---|---|
| ANA-1 | Every Analyst MUST be a function with a typed input (tables plus parameters) and typed output (numbers, intervals, flags, assumptions). | B10 | UT | P0 | AN-1 |
| ANA-2 | A new analysis MUST be added by registering a module with its contract, tests and evaluation cases; free-form code execution MUST NOT be permitted. | B0; B10 | INS | P0 | AN-1 |
| ANA-3 | Every Analyst MUST have unit tests for sparse data, zero baseline, empty segments and short history. | B10 | UT | P0 | AN-1 |
| ANA-4 | Every Analyst MUST be able to return an "insufficient evidence" result instead of a forced answer. | B3; B10 | UT | P0 | AN-1 |
| ANA-5 | Every Analyst MUST be scored against planted ground truth and its module baseline. | D3; C3 | EV | P0 | AN-1 |
 
---
 
## 9. Anomaly Analyst
 
| ID | Requirement | Source | Verification | Pri | Owner |
|---|---|---|---|---|---|
| ANOM-1 | Data prep MUST aggregate to a regular series at the question's grain, drop incomplete boundary periods and flag sparse or truncated history. | B10.1 | UT | P0 | AN-1 |
| ANOM-2 | The baseline MUST be seasonality-aware with a prediction interval, fitted on history that excludes the tested period; calendar effects SHOULD be used when a calendar table exists. | B10.1 | UT, EV | P0 | AN-1 |
| ANOM-3 | When history is too short the module MUST fall back to a trailing median/MAD baseline and say so. | B10.1 | UT | P0 | AN-1 |
| ANOM-4 | Scoring MUST report actual vs expected, % deviation, robust score, interval position and persistence. | B10.1 | UT | P0 | AN-1 |
| ANOM-5 | A change-point check MUST distinguish a level shift from drift or a one-off spike. | B10.1 | UT, EV | P1 | AN-1 |
| ANOM-6 | When scanning many series the module MUST control false discoveries and report the correction used. | B10.1 | UT, EV | P1 | AN-1 |
| ANOM-7 | Below a minimum volume the module MUST return "insufficient evidence". | B10.1 | UT | P0 | AN-1 |
| ANOM-8 | Output MUST contain verdict (unusual / within normal range / insufficient evidence), expected range, deviation, window, threshold, correction and calendar effects considered. | B10.1 | UT | P0 | AN-1 |
| ANOM-9 | The module MUST be compared with a fixed ±2σ trailing-window rule on planted anomalies and negative controls, reporting recall, false-alert rate and detection delay. | D3; D1 | EV | P0 | AN-1 |
 
---
 
## 10. Driver (root-cause) Analyst
 
| ID | Requirement | Source | Verification | Pri | Owner |
|---|---|---|---|---|---|
| DRV-1 | The module MUST define the metric, current period, comparison period and metric tree before analysis. | B10.2 | UT | P0 | AN-1 |
| DRV-2 | Contribution decomposition over mutually exclusive dimensions MUST reconcile to the total change within tolerance. | B10.2; PPR-03.4, 04.2 | UT, EV (reconciliation error) | P0 | AN-1 |
| DRV-3 | For value metrics the module MUST split change into price, volume and mix effects. | B10.2; PPR-04.2 | UT | P1 | AN-1 |
| DRV-4 | Contributions MUST carry bootstrap intervals and sample sizes, and unstable small cells MUST be flagged. | B10.2 | UT | P1 | AN-1 |
| DRV-5 | The module MUST test whether candidate operational indicators moved in the same window (and lagged where data allows) and report association strength and limits. | B10.2 | UT, EV | P1 | AN-1 |
| DRV-6 | Where a comparable unchanged group exists, the module MUST run a difference-in-differences style comparison. | B10.2 | UT, EV | P1 | AN-1 |
| DRV-7 | The module MUST cap dimensions and drill-down depth and correct for the number of candidate drivers examined. | B10.2 | UT | P1 | AN-1 |
| DRV-8 | Output MUST list ranked drivers with share, interval, sample size, evidence grade and the data that would strengthen the claim. | B10.2 | UT | P0 | AN-1 |
| DRV-9 | Every driver analysis MUST end with the causal-boundary note (E5). | B11; E5 | UT, INS | P0 | AN-1 |
| DRV-10 | The module MUST be compared with a "largest absolute change" rule on top-1 and top-3 driver hit rate, with false-driver and confounder-confusion rates reported. | D3; D1 | EV | P0 | AN-1 |
 
---
 
## 11. Risk Analyst (churn risk)
 
| ID | Requirement | Source | Verification | Pri | Owner |
|---|---|---|---|---|---|
| RISK-1 | "Lost" MUST be defined as no purchase in N days after a cut-off date, with N and the active-customer definition taken from the Metric Registry; a dominant one-time-buyer share MUST be stated in every answer. | B10.3 | UT, INS | P0 | AN-1 |
| RISK-2 | Features MUST use only data before the cut-off and splits MUST be chronological (leakage-safe). | B10.3 | UT (leakage test) | P0 | AN-1 |
| RISK-3 | Features SHOULD include recency, frequency, monetary value, tenure, inter-purchase gaps, spend trend, service-quality signals, category and region. | B10.3 | INS | P1 | AN-1 |
| RISK-4 | A heuristic and a regularised logistic baseline MUST be built; a gradient-boosted model ships only if it beats them on the hold-out. | B10.3 | EV | P0 | AN-1 |
| RISK-5 | Evaluation MUST report PR-AUC with prevalence, ROC-AUC, Brier score, calibration and precision/lift at top-k contact capacity. | B10.3; D1 | EV | P0 | AN-1 |
| RISK-6 | Predicted risk MUST be aggregated to business segments with expected revenue at risk (probability × customer value) and resampled intervals. | B10.3 | UT, EV (rank agreement with planted truth) | P0 | AN-1 |
| RISK-7 | Output MUST give ranked segments, drivers labelled as association (not cause), a calibration note and stated limits. | B10.3 | UT | P0 | AN-1 |
| RISK-8 | With too little history or extreme imbalance the module MUST return "insufficient evidence". | B10.3 | UT | P1 | AN-1 |
 
---
 
## 12. Advisor (recommendations)
 
| ID | Requirement | Source | Verification | Pri | Owner |
|---|---|---|---|---|---|
| REC-1 | An Action Library MUST link actions to driver and segment types with preconditions, owner, lead time and cost class. | B10.4; C6 | INS | P0 | AN-1 |
| REC-2 | Ranking MUST be impact × evidence confidence × feasibility, with impact taken from quantified data and not from model opinion. | B10.4 | UT | P0 | AN-1 |
| REC-3 | Each recommendation set MUST offer 2–3 options with assumptions, trade-offs, sensitivity to the key assumption, what to monitor and what data would change the ranking. | B10.4; PPR-03.5 | UT, EV (rubric) | P0 | AN-1 |
| REC-4 | When evidence is weak, the Advisor MUST recommend the cheapest information-gathering step. | B10.4 | UT | P1 | AN-1 |
| REC-5 | The agent MUST recommend only; a person approves, and the agent never executes an action. | B10.4; B13 | INS | P0 | AN-1, FE-1 |
 
---
 
## 13. Evidence, faithfulness and answer contract
 
| ID | Requirement | Source | Verification | Pri | Owner |
|---|---|---|---|---|---|
| EVID-1 | Causal strength MUST be graded 0–4 (fact, concentration, co-movement, specific-to-unit vs comparison group, consistent-with-cause) with only the allowed wording per grade. | B11 | UT, EV | P0 | AN-1, BE-1 |
| EVID-2 | The Evidence Ledger MUST link every numeric or causal-sounding sentence to a query ID or module artifact and a grade. | B12 | UT | P0 | BE-1 |
| EVID-3 | The Synthesizer MUST write from the ledger only and MUST NOT introduce a figure. | B12; B3 | EV (faithfulness) | P0 | BE-1 |
| EVID-4 | The Fact Checker MUST compare every number and date in the narrative with the ledger, remove or relabel unsupported text, and block "caused", "because of", "due to" unless a grade 4 label and its limits are attached. | B11; PPR-06.10 | UT, EV (overclaim set) | P0 | BE-1 |
| EVID-5 | If the narrative cannot be repaired, the system MUST return a template-based answer built from the ledger. | B14 | IT | P1 | BE-1 |
| EVID-6 | Answers MUST separate data-derived findings, interpretation and recommendations. | PPR-04.3 | INS, EV (rubric) | P0 | BE-1 |
| EVID-7 | Every answer MUST contain: route and reason, conclusion, scope, evidence, method and parameters, executed SQL with status and timing, trace, caveats, next action, run metrics. | B12 | IT, INS | P0 | BE-1, FE-1 |
| EVID-8 | All calculations (ratios, growth, margins, variances, statistics) MUST be computed by code. | PPR-04.1; B4 | INS | P0 | AN-1 |
 
---
 
## 14. Operations, cost and latency
 
| ID | Requirement | Source | Verification | Pri | Owner |
|---|---|---|---|---|---|
| OPS-1 | The Trace Store MUST record per step: inputs, outputs, timing, tokens, cost and retries. | C6; D5 | OBS | P0 | BE-1 |
| OPS-2 | The orchestrator MUST enforce per-query budgets for tokens, queries and wall time, visible in the trace. | B15 | IT | P1 | BE-1 |
| OPS-3 | Model tiering MUST use a small model for routing help, slot filling and narration, and a stronger model only for complex SQL and repair. | B15; PPR-05.3 | EV (cost, accuracy) | P1 | BE-1 |
| OPS-4 | Caching MUST use exact-match and slot-based plan keys (normalised metric, dimension, filters, time range), be invalidated on schema or definition change and be scoped by user permission. | B15; R2 | UT, OBS | P1 | BE-1 |
| OPS-5 | Independent module queries SHOULD run in parallel. | B15 | OBS | P2 | BE-1 |
| OPS-6 | Initial latency targets: governed and lookup routes average ≤ 2.5 s on cache miss, ≤ 350 ms on cache hit, P95 ≤ 5 s; analysis-route budgets MUST be set from measurement and documented. | PPR-05.6; R8 | OBS | P1 | BE-1 |
| OPS-7 | Every result MUST record model name and version, commit hash, data snapshot id and date. | D6 | INS | P0 | BE-1 |
| OPS-8 | A query MUST enter the Golden Store only after passing result checks and human approval. | C6 | UT, INS | P1 | BE-2 |
| OPS-9 | User feedback and corrections SHOULD be logged and used to retrain the router and retriever on a schedule, evaluated on the dev set first. | C6; PPR-02.8 | INS | P2 | BE-2 |
 
---
 
## 15. Data and evaluation integrity
 
| ID | Requirement | Source | Verification | Pri | Owner |
|---|---|---|---|---|---|
| DATA-1 | The data MUST come from three layers: public text-to-SQL benchmark databases, a public transactional dataset, and a synthetic enterprise layer. | C1 | INS | P0 | BE-2 |
| DATA-2 | The synthetic generator MUST be code with a seed and configuration, and MUST produce 50-, 200- and 500-table variants. | C2 | IT | P0 | BE-2 |
| DATA-3 | The generator MUST reproduce: cryptic and inconsistent names, stale and duplicate tables, messy values, merged customers, missing foreign keys, soft deletes and test records, late or truncated data, distractor tables, unanswerable asks. | C2 | INS | P0 | BE-2 |
| DATA-4 | The generator MUST write a ground-truth manifest (planted anomalies, drivers with true shares, confounders, negative controls, churn process, seeded SQL traps) that the agent cannot read. | C3 | INS | P0 | BE-2 |
| DATA-5 | There MUST be two snapshots with different seeds and injection settings: build and held-out. | C4 | INS | P0 | BE-2 |
| DATA-6 | Question sets MUST be split into development and locked held-out; public benchmark training splits are for building and dev/test splits for evaluation only. | C4 | INS | P0 | BE-2 |
| DATA-7 | Every dataset MUST have a provenance record (source, URL, version, licence, permitted use, layer); no real person's personal data MAY be used without consent. | C5; Brief | INS | P0 | BE-2 |
| DATA-8 | Before each final run, held-out questions MUST be checked against the Golden Store for leakage. | C4 | UT | P0 | BE-2 |
| DATA-9 | The held-out manifest MUST be readable only by the scoring code. | C4 | INS | P0 | BE-2, AN-1 |
 
---
 
## 16. Evaluation framework
 
| ID | Requirement | Source | Verification | Pri | Owner |
|---|---|---|---|---|---|
| EVAL-1 | The framework MUST implement the D1 metrics: routing, retrieval, value linking, SQL safety and correctness, anomaly, root cause, risk, recommendation, faithfulness, abstention, operations. | D1 | EV | P0 | AN-1 |
| EVAL-2 | The suite MUST cover the D2 categories (governed lookups, joins with messy values, anomaly with negative controls, root cause with confounders, risk, recommendation, refusals, near-misses, ambiguous, causal-overclaim, injection, scale ladder). | D2 | INS | P0 | AN-1 |
| EVAL-3 | The suite MUST compare Baseline A (naive text-to-SQL), Baseline B (grounded SQL only) and System C, with the ablation ladder and module baselines. | D3 | EV | P0 | AN-1 |
| EVAL-4 | The pass rule MUST be: all hard gates zero; SQL correctness above Baseline A with lower hallucination; each Analyst meets its threshold; refusal and over-refusal within thresholds; zero causal overclaims; latency and cost within budget or the trade-off documented. A failure in any item is reported as a failure and categories are never averaged. | D4 | EV | P0 | AN-1 |
| EVAL-5 | The full suite MUST run on every prototype tag and results MUST be committed under `eval/results/<tag>/`. | D5; Brief | INS | P0 | AN-1 |
| EVAL-6 | Reports MUST use paired per-question results, count correct refusals as correct and include refusals and failures in the denominator. | D3 | INS | P0 | AN-1 |
| EVAL-7 | Thresholds MUST be calibrated on the dev set and frozen in `spec/thresholds.md` before the held-out run. | A4; D4 | INS | P0 | AN-1 |
| EVAL-8 | Reports MUST state model name and version, commit hash, snapshot id and date; a target MUST NOT be presented as a result. | D6 | INS | P0 | AN-1 |
 
Initial targets (to be calibrated, per R7 and R14):
 
| Metric | Initial target |
|---|---|
| Hallucination rate (invented table or column) | 0 executed; ≤ 1% generated before validation |
| Schema validity rate | ≥ 98% |
| Retrieval recall@k | ≥ 95% |
| First-pass execution success | ≥ 85% |
| Result-set accuracy | ≥ 88% |
| Abstention accuracy | ≥ 95% |
| Repair success within 2 attempts | ≥ 70% |
| Faithfulness (numeric claims matching ledger) | 100% |
| Latency, cache hit ratio | per OPS-6; hit ratio ≥ 25% in production-like workload |
 
---
 
## 17. Product surface and delivery
 
| ID | Requirement | Source | Verification | Pri | Owner |
|---|---|---|---|---|---|
| UI-1 | The web app MUST provide a question box and an answer view showing route, SQL, method, numbers and caveats. | F2; Brief | OBS | P0 | FE-1 |
| UI-2 | A step-by-step trace view MUST be available for any run. | F2; Brief | OBS | P0 | FE-1 |
| UI-3 | A metrics page MUST show the problem metrics next to accuracy, p50/p95 latency and cost per route and per stage, from the running system. | D5; Brief | OBS | P0 | FE-1 |
| UI-4 | Clarifications, refusals and "insufficient evidence" results MUST be rendered clearly with what is missing. | B14 | OBS | P0 | FE-1 |
| UI-5 | The UI MUST let a person approve or reject operational actions and review Golden Store candidates. | B13 | OBS | P1 | FE-1 |
| UI-6 | The system MUST be deployed at a public URL that someone outside the team can use. | Brief; F2 | OBS | P0 | FE-1, BE-1 |
| DEL-1 | Each prototype MUST be a tagged version (`v0.1` to `v1.0`) with committed results. | F1; Brief | INS | P0 | all |
| DEL-2 | `README.md` MUST let someone set up and run the system from scratch. | Brief | INS | P0 | all |
| DEL-3 | `APPROACH.md` (max 4 pages, sections A–E, architecture and workflow diagrams, glossary) and `ARTEFACT.md` (sections a–j) MUST be delivered. | Brief | INS | P0 | all |
| DEL-4 | The prompt pack MUST keep every runtime prompt as a versioned file with a changelog. | Brief | INS | P0 | BE-1 |
| DEL-5 | `agent_setup/` MUST contain the coding-agent rules, specification, subagents, hooks and reusable prompts. | Brief | INS | P0 | all |
| DEL-6 | Coding-agent session traces MUST be submitted to the assessment platform at each tag. | Assessment | INS | P0 | all |
 
---
 
## 18. Coverage check against the PRD
 
| PRD item | Covered by |
|---|---|
| PPR-01 Enterprise scale | GRD-1 to GRD-12, DATA-2, DATA-3 |
| PPR-02 ML/AI components | GRD-4, GRD-5, GRD-8, GRD-10, RTE-2, RTE-5, OPS-9 |
| PPR-03 Business questions | RTE-5, RTE-6, DRV-1 to DRV-10, REC-1 to REC-5 |
| PPR-04 Insight generation | EVID-6, EVID-8, ANOM-1 to ANOM-9, DRV-3 (optimisation deferred, R6) |
| PPR-05 Accuracy, latency, cost | SQL-8, OPS-1 to OPS-6, section 16 targets |
| PPR-06 Reliability and safety | SAFE-1 to SAFE-8, SQL-2 to SQL-13, ABS-1, EVID-4 |
| PRD section 5 modules 5.1–5.12 | RTE, GRD, SQL, EVID groups |
| PRD section 6 nine skills | Implemented as registered components with the verification gates above |
| PRD Phases 2–5 | OPS-4, RTE-5, ANOM, DRV, REC; optimisation and learning loop at P2 |
 
## 19. Open questions
 
1. R1: which engine and dialect is the primary one for development and the deployed demo?
2. R14: confirm the stricter section 7 targets are the initial ones.
3. Which public benchmark databases and which transactional dataset (licence check pending, DATA-1, DATA-7)?
4. Hosted model API or self-hosted model (changes every latency and cost figure, F2)?

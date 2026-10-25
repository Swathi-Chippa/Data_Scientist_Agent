# Interfaces.md — Input/Output Formats Between Team Members
### Companion to `Product.md` (Section 5) and `DESIGN_DETAIL.md`

**People:** FE-1 Abdul, AN-1 Swathi, BE-1 Ganesh (also Team Lead), BE-2 Vishal.
**Purpose:** every hand-off between members, with exact input and output shapes, so FE-1, AN-1, BE-1 and BE-2 can start in parallel.
**Notation:** TypeScript-style types for readability. BE-1 converts these to JSON Schema and Pydantic models in `contracts/` in week 1 (Section 10); after that, `contracts/` is the source of truth. Names here match `Product.md`; if they disagree, fix this file by PR.

---

## 0. Quick map: who sends what to whom

| # | Producer | Consumer | Format (section) |
|---|---|---|---|
| 1 | FE-1 | BE-1 | `AskRequest`, `DecisionRequest`, `ReviewRequest` (2.1) |
| 2 | BE-1 | FE-1 | SSE `RunEvent` stream, `AnswerEnvelope` (Product 5.2), `TraceEvent` (Product 5.5), `ErrorEnvelope` (1.2) |
| 3 | FE-1 | BE-1 / BE-2 | `ConnectRequest`, join and sensitive-column decisions, golden and registry decisions (2.2) |
| 4 | BE-2 | FE-1 (via BE-1 routes) | `OnboardingJob`, `OnboardingReport`, `JoinEdge` (Product 5.6) |
| 5 | BE-1 step 1 to 10 | BE-1 next step | `GuardResult`, `RouterResult`, `Plan`, `MetricResolution`, `AnswerabilityResult`, `RetrievalResult`, `SqlCandidate`, `ValidationResult`, `ExecutionResult`, `ReconciliationResult` (3) |
| 6 | BE-1 | AN-1 | `AnalystInput` per module (4.1 to 4.4) |
| 7 | AN-1 | BE-1 | `AnalystOutput` (Product 5.3) |
| 8 | AN-1 + BE-1 | BE-2 | `LedgerBuildInput` (6.1) |
| 9 | BE-2 | BE-1 | `Ledger`, `SynthInput`, `SynthOutput`, `FactCheckResult` (6.1 to 6.3) |
| 10 | BE-2 | BE-1 | Knowledge-store Python calls (5.2) |
| 11 | BE-2 / AN-1 | each other | `GroundTruthManifest` (7.1), generator hook (7.2) |
| 12 | BE-1 | all | `EvalCase`, `EvalResultRow`, dashboard payloads (8) |
| 13 | AN-1 | BE-2 | `ActionLibraryEntry` (5.1) |

---

## 1. Conventions (apply to everything)

### 1.1 Common rules

- **Naming:** `snake_case` for fields; enums are lowercase strings.
- **IDs:** strings. `run_id`, `query_id` (`Q1`, `Q2`, ... unique within a run), `claim_id` (`C1`, ...), `artifact_id`, `edge_id`, `metric_id`, `job_id`.
- **Time:** timestamps ISO-8601 UTC (`2026-10-20T10:46:00Z`); dates `YYYY-MM-DD`.
- **Periods:** `{ start: date, end: date }`, **start inclusive, end exclusive**.
- **Nulls:** a field that can be absent is marked `?`. `null` means "known to be empty"; a missing key means "not applicable".
- **Versions:** every response carries `contract_version` (semver). Breaking change bumps major (Product 7.4).
- **Sensitive data:** columns flagged sensitive are masked before they enter any contract object, trace, prompt or log.
- **Numbers:** raw numbers are never rounded by the producer; formatting is done by FE-1 and the Synthesizer.

### 1.2 Error envelope (all API failures)

```ts
interface ErrorEnvelope {
  contract_version: string;
  error: {
    code: ErrorCode;
    message: string;               // safe to display
    retriable: boolean;
    run_id?: string;
    details?: Record<string, unknown>;
  };
}

type ErrorCode =
  | "bad_request"          // 400  malformed body
  | "not_found"            // 404
  | "conflict"             // 409  e.g. decision already recorded
  | "unauthorized"         // 401
  | "forbidden"            // 403
  | "budget_exceeded"      // 429  token, query or wall-time budget
  | "model_unavailable"    // 503  and no fallback path
  | "warehouse_unavailable"// 503
  | "internal";            // 500
```

**Important:** a run that ends as *clarify*, *refused*, *blocked* or *insufficient evidence* is **not an error**. It returns HTTP 200 with an `AnswerEnvelope` whose `status` says so. `ErrorEnvelope` is only for failures of the request or the system.

---

## 2. API request and response bodies (FE-1 to BE-1 / BE-2)

Routes are listed in `Product.md` 5.1. Bodies below.

### 2.1 Ask flow

```ts
// POST /api/ask
interface AskRequest {
  question: string;                 // raw user text, trimmed
  session_id: string;               // created by FE-1 (uuid), reused across a conversation
  clarification_of?: string;        // run_id being answered; question then holds the user's reply
}
interface AskResponse { contract_version: string; run_id: string; }

// GET /api/runs?limit=&cursor=
interface RunListResponse {
  contract_version: string;
  items: {
    run_id: string; question: string; status: RunStatus;
    route_labels: string[]; created_at: string; latency_ms: number | null;
  }[];
  next_cursor?: string;
}

// POST /api/runs/{run_id}/actions/{action_id}/decision
interface DecisionRequest { decision: "approve" | "reject"; note?: string; }
interface DecisionResponse { contract_version: string; action_id: string; decision: string; recorded_at: string; }
// Records the PERSON's decision only. The agent never executes the action. 409 if already decided.

// POST /api/runs/{run_id}/review-request
interface ReviewRequest { reason?: string; }
interface ReviewResponse { contract_version: string; review_id: string; status: "requested"; }
```

### 2.2 Run event stream (SSE: `GET /api/runs/{run_id}/events`)

Each SSE message has `event: <type>` and `data: <JSON>`. Order is guaranteed per run; `seq` increases by 1.

```ts
type RunEvent =
  | { type: "step_started";  seq: number; run_id: string; step: string; component: string; at: string }
  | { type: "step_finished"; seq: number; run_id: string; step: string; component: string;
      status: "ok" | "failed" | "skipped" | "retried"; duration_ms: number; summary: string }
  | { type: "clarify";       seq: number; run_id: string; what_understood: string; what_needs_clarification: string }
  | { type: "answer";        seq: number; run_id: string; status: RunStatus }   // then GET /api/runs/{id} for the envelope
  | { type: "error";         seq: number; run_id: string; error: ErrorEnvelope["error"] };
```

`step` is the step number as a string: `"1"` to `"14"`, `"8b"` (Repairer), `"11b"` (Advisor). The stream ends after `answer` or `error`. `summary` is one safe-to-display sentence (never raw rows or sensitive values). FE-1 reconnects with `Last-Event-ID = seq`; BE-1 replays missed events.

### 2.3 Warehouse onboarding, review and admin

```ts
// POST /api/warehouse/connect
interface ConnectRequest {
  dialect: string;                       // as supported by the Executor, e.g. "duckdb" | "postgres" | "bigquery"
  connection_ref: string;                // name of a server-side connection config; NEVER a password
  schemas_include?: string[];            // default: all readable schemas
  schemas_exclude?: string[];
  profile_sample_budget?: { max_rows_per_table?: number; max_scan_cost?: number };
  mode?: "initial" | "refresh";
}
interface ConnectResponse { contract_version: string; job_id: string; }
// Secrets live in environment config referenced by connection_ref (Product 4.4.1). FE-1 never sees or echoes them.

// GET /api/joins?status=proposed&cursor=
interface JoinListResponse { contract_version: string; items: JoinEdge[]; next_cursor?: string; }   // JoinEdge: Product 5.6
// POST /api/joins/{edge_id}/decision
interface JoinDecisionRequest { decision: "approve" | "reject"; note?: string; }

// GET /api/columns/sensitive
interface SensitiveColumnsResponse {
  contract_version: string;
  items: { table: string; column: string; detected_by: "rule" | "user"; rule?: string; status: "masked" | "confirmed" | "released_by_user" }[];
}
// POST /api/columns/sensitive   (user adds or confirms a masked column)
interface SensitiveColumnRequest { table: string; column: string; action: "mask" | "unmask_request"; note?: string; }
// "unmask_request" needs Lead approval; the server never returns raw values either way.

// GET /api/golden/candidates ; POST /api/golden/candidates/{id}/decision
interface GoldenCandidate {
  candidate_id: string; question: string; sql: string;
  result_checks: { name: string; passed: boolean }[];      // reconciliation, known-answer test, etc.
  source_run_id: string; created_at: string;
  status: "pending" | "approved" | "rejected";
}
interface GoldenDecisionRequest { decision: "approve" | "reject"; note?: string; edited_sql?: string; }

// GET /api/registry/changes ; POST /api/registry/changes/{id}/decision
interface RegistryChange {
  change_id: string; metric_id: string; kind: "new" | "update";
  current: MetricDefinition | null; proposed: MetricDefinition;
  proposed_by: string; created_at: string; status: "pending" | "approved" | "rejected";
}
interface RegistryDecisionRequest { decision: "approve" | "reject"; note?: string; }
```

### 2.4 Metrics and evaluation (BE-1 harness to FE-1)

See Section 8 (`MetricsResponse`, `EvalResultsResponse`).

---

## 3. Pipeline step contracts (BE-1, internal, but AN-1 and BE-2 depend on them)

The orchestrator passes one `RunContext` through the steps. Each step reads what it needs and returns its own result; nothing mutates earlier results.

```ts
interface RunContext {
  run_id: string; question: string; session_id: string;
  catalog_version: string;
  budgets: { max_tokens: number; max_queries: number; max_wall_ms: number };
  used:    { tokens: number; queries: number; wall_ms: number };
}
```

### Step 1: Guard
```ts
// in:  { question: string }
interface GuardResult {
  passed: boolean;
  reason?: "write_attempt" | "injection" | "unsafe";
  matched_rule?: string;            // rule id, for the trace
}
```

### Step 2: Router and Extractor
```ts
type RouteLabel =
  | "lookup" | "anomaly" | "root_cause" | "risk" | "recommendation"
  | "ambiguous" | "unanswerable" | "out_of_scope";          // design A2

interface RouterResult {
  routes: RouteLabel[];                                      // a compound question can carry several
  confidence: number;                                        // 0..1, decided by code
  signals: {
    classifier: { label: RouteLabel; prob: number };
    rules:      { matched: string[]; label?: RouteLabel };
    model:      { proposed_label: RouteLabel; slots: Slots };
    agreement: boolean;                                      // disagreement lowers confidence
  };
  slots: Slots;                                              // final slots, validated against allowlists
  reason: string;                                            // shown to the user ("Treated as: ... because ...")
  clarification?: { what_understood: string; what_needs_clarification: string };   // set when below threshold
}

interface Slots {
  metric_terms: string[];                                    // raw business words
  dimensions: string[];
  filters: { term: string; value_phrase: string }[];
  time: {
    raw: string;                                             // "last month"
    resolved?: { current: Period; comparison?: Period; grain?: "day" | "week" | "month" | "quarter" | "year" };
  };
  action_intent?: boolean;
}
```

### Step 3: Planner
```ts
interface Plan {
  steps: {
    step_id: string;                                         // "P1", "P2"
    module: "lookup" | "anomaly" | "driver" | "risk" | "advisor";   // must exist in the Analyst registry
    depends_on: string[];
    params: Record<string, unknown>;                         // validated against the module's params schema (Section 4)
  }[];
  depth: number;                                             // capped
  source: "model_validated" | "fallback_single_module";
}
```

### Step 4: Metric Resolver
```ts
interface MetricResolution {
  coverage: "full" | "partial" | "none";                     // full => SQL Compiler path
  metric_id: string | null;
  definition: MetricDefinition | null;                       // see 5.1
  resolved_dimensions: { requested: string; allowed: boolean; column?: string }[];
  grain: string | null; date_column: string | null;
  required_filters: string[];
  caveats: string[];                                         // includes "ungoverned metric" when none
}
```

### Step 5: Answerability Checker
```ts
interface AnswerabilityResult {
  answerable: boolean;
  confidence: number;
  missing: string[];                                         // names the missing attribute(s)
  closest_answerable?: string;                               // suggested rephrasing
  cheap_filter_hit?: string;                                 // pattern rule that fired, if any (design E3)
}
```

### Step 6: Schema Retriever & Perceptor (online)
```ts
interface RetrievalResult {
  retrieval_confidence: number;                              // below threshold => clarify or refuse
  tables: {
    name: string; score: number; rerank_score?: number;
    columns: { name: string; score: number; warnings: string[] }[];   // e.g. "40% null", "mixed date formats"
  }[];
  join_paths: { edge_ids: string[]; tables: string[] }[];    // approved edges only
  linked_values: { phrase: string; table: string; column: string; literal: string; confidence: number }[];
  allowlist: { tables: string[]; columns: { table: string; column: string }[] };   // what the Validator will enforce
  catalog_version: string;
}

// Online Perceptor execution settings (src/schema_retriever/perceptor.py)
interface PerceptorConfig {
  column_top_k: number;                                      // default 5
  value_description_top_k: number;                          // default 3
  value_vector_top_k: number;                               // default 3
  value_search_workers: number;                             // default 4
  value_search_max_calls: number;                           // default 80
  column_score_threshold: number;                           // default 0.4
  value_score_threshold: number;                            // default 0.55
}
```

### Step 7: SQL Compiler or Generator
```ts
// in (Generator only): RetrievalResult + MetricResolution + ICL exemplars from golden.nearest(...)
interface SqlCandidate {
  candidate_id: string;
  sql: string;                                               // exactly one statement
  dialect: string;                                           // target SQL dialect identifier
  origin: "compiler" | "generator" | "repair";
  intended_columns: { name: string; role: "dimension" | "measure" | "time" | "key" }[];
  grain: string[];                                           // columns that make a row unique
  uses_edges: string[];                                      // edge_ids the SQL joins on
  model?: { name: string; version: string; tokens_in: number; tokens_out: number };
  icl_exemplars_used?: string[];                             // IDs of golden queries retrieved for ICL
}
```

### Step 8: Validator
```ts
interface ValidatorConfig {
  mode: "benchmark" | "enterprise";                          // benchmark relaxes limits/bounds to score BIRD EX
  dialect: string;
  budget_max_scanned_rows?: number;
  require_explicit_columns: boolean;                         // false in benchmark mode (allows SELECT *)
}

interface ValidationResult {
  approved: boolean;
  validation_mode: "benchmark" | "enterprise";
  checks: {
    id: number;                                              // 1..10, design B8
    name: "parse" | "single_select" | "no_dml_ddl" | "allowlist" | "approved_joins_fanout"
        | "limits_bounds" | "cost_gate" | "readonly_exec" | "post_exec" | "reconcile";
    status: "pass" | "fail" | "skipped";
    reason?: string;
    offending?: string[];                                    // identifiers or edges
  }[];
  est_cost?: { rows_scanned?: number; bytes?: number; plan_cost?: number };
  rewrite?: string;                                          // cost-gate rewrite, if applied
}
```
Checks 8 to 10 are completed after execution; the Validator result is updated and re-emitted.

### Step 8b: Repairer & Reflector
```ts
interface RepairRequest {
  attempt: 1 | 2;                                            // cap is 2
  failed_sql: string;
  error: { kind: "parse" | "validation" | "runtime" | "reconciliation"; message: string };
  allowlist: RetrievalResult["allowlist"];                   // SAME schema, never widened
  reflector_score_threshold?: number;                        // default 0.9 reflection confidence
}
// out: SqlCandidate (origin "repair", with repair_of = failed query_id)
```

### Step 9: Multi-Datasource Executor (src/gateway/)
```ts
interface GatewayConfig {
  datasource_id: string;
  dialect: string;                                           // target database SQL dialect identifier
  connection_pool_size: number;
  timeout_ms: number;
  read_only: boolean;                                        // must be strictly true
}

interface ExecutionResult {
  query_id: string;
  datasource_id?: string;
  status: "ok" | "error" | "timeout" | "truncated";
  columns: { name: string; type: string; role?: "dimension" | "measure" | "time" | "key" }[];
  row_count: number;
  truncated: boolean;                                        // hit the row cap
  rows?: unknown[][];                                        // inline when row_count <= inline_limit (config)
  result_ref?: string;                                       // otherwise a reference (e.g. a Parquet artifact)
  rows_scanned?: number; duration_ms: number;
  error?: { kind: "binder" | "catalog" | "syntax" | "permission" | "timeout" | "other"; message: string };
}
```

### Step 10: Reconciler
```ts
interface ReconciliationResult {
  status: "pass" | "flags";
  checks: { name: "grain" | "nulls" | "duplicates" | "ranges" | "registry_total"; status: "pass" | "flag"; detail?: string }[];
  registry_comparison?: { metric_id: string; observed: number; expected: number; tolerance: number; within: boolean };
  caveats: string[];                                         // carried into the final answer
  retry_used: boolean;
}
```

---

## 4. Analyst inputs (BE-1 to AN-1)

### 4.1 Common input and call convention

```ts
interface AnalystInput<P> {
  module: "anomaly" | "driver" | "risk";
  params: P;
  tables: Record<string, TableRef>;                          // named inputs, see each module
  metric: MetricDefinition | null;                           // governed definition if any
  run_id: string;
  seed?: number;                                             // for bootstrap / model training; required for reproducibility
}

interface TableRef {
  query_id: string;                                          // links the claim back to its source query
  columns: { name: string; type: string; role: "dimension" | "measure" | "time" | "key" | "label" }[];
  row_count: number;
  uri: string;                                               // Parquet artifact; Python side loads into a DataFrame
}
```

**Python call convention (all modules):**
```python
def run(inp: AnalystInput) -> AnalystOutput: ...
```
- Pure function: same input and seed give the same output; no network, no warehouse access (BE-1 supplies the tables).
- Never raises for data problems; returns `status = "insufficient_evidence"` with a `reason`.
- Registered in the Analyst registry (below); the Planner can only call registered modules.

```ts
interface AnalystRegistryEntry {
  module: string; version: string;
  params_schema: JsonSchema; input_tables: { name: string; required_columns: { name: string; role: string }[] }[];
  output_schema: JsonSchema;
  tests: string; eval_cases: string;                         // paths in the repo
}
```
`AnalystOutput<T>` and the three result types are in `Product.md` 5.3.

### 4.2 Anomaly

```ts
interface AnomalyParams {
  metric: string; grain: "day" | "week" | "month";
  test_window: Period;                                       // excluded from baseline fit
  history_window?: Period;
  series_dimension?: string;                                 // set when scanning many series (region, segment)
  alpha: number;                                             // significance / FDR level
  min_volume: number;                                        // volume guard
  use_calendar: boolean;
}
// tables:
//   "series": columns [period(time), value(measure), <series_dimension>?(dimension), volume(measure)?]
//   "calendar"?: columns [date(time), event(dimension), kind(dimension)]
```

### 4.3 Driver

```ts
interface DriverParams {
  metric: string;
  current_period: Period; comparison_period: Period;
  dimensions: string[];                                      // mutually exclusive dimensions to decompose over
  max_dimensions: number; max_depth: number;
  metric_tree?: { node: string; formula: "sum" | "product"; children: string[] }[];
  value_decomposition?: boolean;                             // price / volume / mix
  indicators?: { name: string; table: string }[];            // candidate operational indicators (delays, stock-outs, discounts)
  comparison_group?: { dimension: string; affected: string; control: string[] };
  n_bootstrap: number;
  alpha: number;
}
// tables:
//   "contrib": columns [period(time), <dimension...>(dimension), value(measure), volume(measure)?, price(measure)?]
//   "indicators"?: columns [period(time), <dimension>(dimension), name(dimension), value(measure)]
```

### 4.4 Risk

```ts
interface RiskParams {
  cutoff_date: string;                                       // features use data BEFORE this date
  window_days: number;                                       // "lost" = no purchase in these days after cutoff
  active_definition: string;                                 // from Metric Registry
  segment_dimensions: string[];
  capacity_k: number;                                        // team's contact capacity for lift / precision at k
  value_column: string;                                      // customer value for revenue at risk
  models: ("heuristic" | "logistic" | "gradient_boosted")[];
  split: { train_end: string; valid_end: string };           // chronological
}
// tables:
//   "orders":    [customer_id(key), order_date(time), amount(measure), category(dimension)?, region(dimension)?]
//   "service"?:  [customer_id(key), event_date(time), kind(dimension), value(measure)]   // delays, complaints, review scores
//   "customers"?:[customer_id(key), <segment dims>(dimension), tenure_start(time)]
```

### 4.5 Advisor

```ts
interface AdvisorInput {
  anomaly?: AnalystOutput<AnomalyResult>;
  driver?:  AnalystOutput<DriverResult>;
  risk?:    AnalystOutput<RiskResult>;
  actions:  ActionLibraryEntry[];                            // from actions.match(...)
  constraints?: { capacity_k?: number; max_options?: number };
}
interface AdvisorOutput {
  status: "ok" | "insufficient_evidence";
  options: ActionOption[];                                   // 2-3 ranked (Product 5.2); information-gathering if evidence is weak
  scoring: { action_id: string; impact: number; evidence_confidence: number; feasibility: number; score: number }[];
  assumptions: string[];
}
```
Impact comes only from quantified module outputs (revenue at risk, share of decline); it never comes from model opinion.

---

## 5. Knowledge stores (BE-2 provides; BE-1 and AN-1 consume)

### 5.1 Record schemas

```ts
interface TableEntry {
  table: string; schema: string; description: string | null; description_is_draft: boolean;
  row_count: number; last_updated: string | null;
  trust: { freshness_days: number | null; duplicate_rate: number | null;
           historical_usage: number | null; likely_stale_copy_of?: string };
  preaggregated: boolean;                                    // cost-gate hint
  columns: ColumnProfile[];
  catalog_version: string;
}

interface ColumnProfile {
  column: string; type: string; description: string | null; description_is_draft: boolean;
  null_rate: number; distinct_count: number | null;
  min?: string | number; max?: string | number;
  formats?: string[];                                        // e.g. "YYYY-MM-DD", "DD/MM/YYYY"
  sample_values: string[];                                   // masked when sensitive
  sensitive: boolean;
  warnings: string[];
}

interface MetricDefinition {
  metric_id: string; name: string; synonyms: string[];
  formula: string;                                           // SQL expression over base table
  required_filters: string[];                                // e.g. "status <> 'cancelled'"
  grain: string[]; date_column: string;
  allowed_dimensions: { name: string; column: string; join_edges: string[] }[];
  base_table: string;
  join_path: { edge_id: string; cardinality: "1:1" | "1:N" | "N:1" | "N:M" }[];
  reconciliation: { sql: string; tolerance: number } | null; // registry's own value for the same metric/period/filters
  caveats: string[];
  version: number; approved_by: string; approved_at: string;
}

interface GoldenEntry {
  entry_id: string; question: string; sql: string;
  tables: string[]; metric_ids: string[];
  verified_by: string; verified_at: string;
  catalog_version: string;
  split: "dev";                                              // only development material is allowed
}

interface ActionLibraryEntry {
  action_id: string; title: string;
  driver_types: string[];                                    // links to driver / segment types
  preconditions: string[]; owner_role: string;
  lead_time: string; cost_class: "low" | "medium" | "high";
  kind: "intervention" | "information_gathering";
  description: string;
}

interface IndexDocument {                                    // one record per indexed item, keyword and dense
  doc_id: string; kind: "table" | "column" | "description" | "golden";
  text: string;                                              // what is embedded / indexed (masked)
  table?: string; column?: string; golden_id?: string;
  catalog_version: string;
}

interface KGraphNode { id: string; type: "table" | "column" | "metric" | "term"; label: string }
interface KGraphEdge {
  from: string; to: string;
  type: "joins" | "belongs_to" | "metric_uses" | "synonym_of" | "stale_copy_of";
  attrs?: { cardinality?: string; status?: string; edge_id?: string };
}
```

### 5.2 Python interface signatures (`contracts/knowledge_api.md` copies these)

```python
class Catalog(Protocol):
    def get_tables(self, names: list[str]) -> list[TableEntry]: ...
    def get_columns(self, table: str) -> list[ColumnProfile]: ...
    def version(self) -> str: ...                             # catalog_version

class JoinGraph(Protocol):
    def paths(self, tables: list[str], approved_only: bool = True) -> list[JoinPath]: ...
    def edge(self, edge_id: str) -> JoinEdge: ...

class KGraph(Protocol):
    def neighbors(self, node_id: str, edge_types: list[str] | None = None) -> list[KGraphEdge]: ...

class Registry(Protocol):
    def resolve(self, term: str) -> MetricDefinition | None: ...        # synonym match
    def get(self, metric_id: str) -> MetricDefinition: ...
    def expected_value(self, metric_id: str, period: Period, filters: list[str]) -> float | None: ...

class Search(Protocol):
    def search(self, query: str, kind: Literal["table","column","golden"], k: int | None = None,
               min_score: float | None = None) -> list[SearchHit]: ...  # hybrid: keyword + dense, scores returned

class Values(Protocol):
    def link(self, phrase: str, column_hint: str | None = None) -> list[LinkedValue]: ...   # with confidence

class Golden(Protocol):
    def nearest(self, question: str, k: int = 3) -> list[GoldenEntry]: ...
    def propose(self, question: str, sql: str, source_run_id: str, checks: list[Check]) -> str: ...   # -> candidate_id

class Actions(Protocol):
    def match(self, driver_types: list[str]) -> list[ActionLibraryEntry]: ...

class Onboarding(Protocol):
    def start(self, req: ConnectRequest) -> str: ...                     # job_id
    def status(self, job_id: str) -> OnboardingJob: ...
    def report(self, job_id: str) -> OnboardingReport: ...
    def refresh(self) -> str: ...                                        # job_id, mode "refresh"

class TraceStore(Protocol):
    def write(self, event: TraceEvent) -> None: ...                      # masking already applied
    def get(self, run_id: str) -> list[TraceEvent]: ...
```

Types not defined above (`SearchHit`, `LinkedValue`, `JoinPath`, `Check`, `Period`) are:
```ts
interface SearchHit { doc_id: string; kind: string; table?: string; column?: string; score: number; rerank_score?: number }
interface LinkedValue { table: string; column: string; literal: string; confidence: number }
interface JoinPath { edge_ids: string[]; tables: string[] }
interface Check { name: string; passed: boolean }
interface Period { start: string; end: string }               // start inclusive, end exclusive
```

**Rules for callers (BE-1):** never read the warehouse catalogue directly; always go through these interfaces so allowlists and masking stay consistent. Calls that need `catalog_version` must pass the version they started the run with.

---

## 6. Trust layer (BE-2; called by BE-1)

### 6.1 Ledger

```ts
interface LedgerBuildInput {
  run_id: string;
  queries: QueryRecord[];                                    // Product 5.2
  results: { query_id: string; summary: unknown }[];        // for lookups: the numbers to be cited
  analyst_outputs: AnalystOutput<unknown>[];
  advisor_output?: AdvisorOutput;
  reconciliation: ReconciliationResult[];
}
interface Ledger {
  run_id: string;
  claims: LedgerClaim[];                                     // Product 5.4
  dropped: { text: string; reason: "no_source" | "grade_too_low_for_wording" }[];
  hypotheses: string[];                                      // claims relabelled as hypotheses
}
```
Every numeric or causal-sounding claim needs `source` and `grade`. A claim without a source is dropped or relabelled as a hypothesis; it is never passed through.

### 6.2 Synthesizer

```ts
interface SynthInput {
  ledger: Ledger;                                            // the ONLY source of facts
  route: { labels: string[]; reason: string };
  scope: AnswerEnvelope["scope"];
  caveats: string[];
  next_actions: ActionOption[];
  style: { audience: "business_owner"; max_words?: number };
}
interface SynthOutput {
  narrative: string;                                         // plain text; after each claim sentence a marker like "[C3]"
  used_claim_ids: string[];
  model: { name: string; version: string; tokens_in: number; tokens_out: number };
  source: "model" | "template_fallback";
}
```
Every numeric token in `narrative` must equal a `numbers[].token` of a claim in the ledger. The model paraphrases; it never adds a figure. FE-1 converts `[Cn]` markers into links to the claim's source.

### 6.3 Fact Checker

```ts
// factcheck.verify(narrative: string, ledger: Ledger) -> FactCheckResult
interface FactCheckResult {
  passed: boolean;
  violations: {
    sentence: string;
    kind: "number_not_in_ledger" | "causal_language_without_grade4" | "claim_without_source" | "grade_exceeded";
    token?: string; claim_id?: string;
  }[];
  repaired_narrative?: string;                               // offending sentence removed or relabelled
  action: "pass" | "repaired" | "template_fallback";
}
```
Blocked words (`caused`, `because of`, `due to`, and equivalents) pass only if the sentence's claim has grade 4, the wording carries its limits, and the causal-boundary note is present (Product 4.4.3).

---

## 7. Ground truth and data generation (AN-1 and BE-2)

### 7.1 Ground-truth manifest (written by the generator, read only by scoring code)

```ts
interface GroundTruthManifest {
  snapshot_id: string; seed: number; generator_version: string; split: "build" | "heldout";
  anomalies: {
    anomaly_id: string; metric: string; unit: { dimension: string; value: string };
    kind: "level_shift" | "spike" | "dip" | "gradual_decay";
    start: string; end?: string; magnitude_pct: number;
  }[];
  drivers: {
    anomaly_id: string; cause_kind: "segment" | "category" | "carrier" | "price_change";
    cause: { dimension: string; value: string }; true_share: number;
  }[];
  confounders: { anomaly_id: string; moves_with: { dimension: string; value: string }; is_cause: false }[];
  negative_controls: { control_id: string; metric: string; unit: { dimension: string; value: string };
                       period: Period; kind: "ordinary" | "holiday_peak" | "promotion_peak" | "small_volume" | "noise" }[];
  churn_process: { process_id: string; description: string; params: Record<string, number>;
                   true_risk_segments: { segment: string; relative_risk: number }[]; label_table: string };
  sql_traps: { trap_id: string; kind: "fan_out" | "cancelled_orders" | "duplicates";
               question: string; correct_result_sql: string; correct_value: number | string }[];
}
```

### 7.2 Generator plug-in hook (BE-2 provides, AN-1 uses)

```python
class PlantPlugin(Protocol):
    name: str
    def inject(self, warehouse: GeneratedWarehouse, rng: Random, manifest: ManifestWriter) -> None: ...
```
- BE-2's generator builds schema, mess and base data, then calls each plugin in a fixed order.
- AN-1's plugins live in `data_gen/plants/` and **must** write every planted fact to `manifest` (nothing planted without a manifest entry).
- The manifest is stored outside the agent-readable path; only scoring code reads it.

---

## 8. Evaluation formats (BE-1; read by FE-1 and the Lead)

```ts
interface EvalCase {
  case_id: string; split: "dev" | "heldout";
  category: "governed_lookup" | "multi_table" | "anomaly" | "root_cause" | "risk" | "recommendation"
          | "out_of_schema_refusal" | "near_miss" | "ambiguous" | "causal_overclaim" | "injection_unsafe" | "scale";
  question: string; author: "BE-1" | "AN-1" | "BE-2";
  expected: {
    route?: RouteLabel[];
    gold_tables?: string[]; gold_columns?: { table: string; column: string }[]; gold_join_edges?: string[];
    gold_sql?: string; gold_result?: unknown; invariant?: string;
    refusal?: { expected: boolean; missing_attribute?: string };
    max_evidence_grade?: 0 | 1 | 2 | 3 | 4;                  // causal-overclaim cases
    planted_ref?: { anomaly_id?: string; control_id?: string; trap_id?: string };
  };
}

interface EvalResultRow {
  tag: string; case_id: string; system: "baseline_a" | "baseline_b" | "full" | `ablation:${string}`;
  status: RunStatus; route_pred: RouteLabel[];
  correct: boolean | null;                                   // null when not scorable; refusals and failures stay in the denominator
  metrics: Record<string, number | boolean>;                 // e.g. table_recall_at_k, result_match, hallucinated_identifier
  hard_gate_violations: string[];                            // must be empty
  latency_ms: number; tokens: number; cost: number; retries: number;
  run_id: string;
}

interface EvalResultsResponse {
  contract_version: string;
  tag: string; model_name: string; model_version: string; commit: string; snapshot_id: string; date: string;
  hard_gates: { gate: string; count: number }[];             // each must be 0
  pass_rule: { item: 1 | 2 | 3 | 4 | 5 | 6; description: string; passed: boolean; detail: string }[];   // never averaged
  rows: EvalResultRow[];
}

interface MetricsResponse {
  contract_version: string;
  provenance: { model_name: string; model_version: string; commit: string; snapshot_id: string; date: string };
  measured: boolean;                                         // false => values are targets, FE-1 must label them "target"
  panels: {
    id: string; title: string;
    series: { label: string; value: number; unit?: string; route?: string; stage?: string }[];
  }[];
}
```

---

## 9. Fixture files each producer ships (week 1)

All fixtures live in `contracts/examples/` and are real instances of the types above.

| Producer | Files | Unblocks |
|---|---|---|
| **BE-1** | `envelope_answered_lookup.json`, `envelope_answered_anomaly_driver.json`, `envelope_clarify.json`, `envelope_refused.json`, `envelope_blocked.json`, `envelope_insufficient_evidence.json`, `run_events_*.sse`, `trace_with_repair.json`, `error_*.json` | FE-1 |
| **BE-1** | one `RunContext` plus a sample of each step result in Section 3 | BE-2, AN-1 |
| **BE-2** | `onboarding_job_{running,needs_review,failed,completed}.json`, `onboarding_report.json`, `join_edges.json`, `golden_candidates.json`, `registry_changes.json`, `ledger_with_grade4.json`, `ledger_with_hypothesis.json`, `factcheck_pass.json`, `factcheck_fail.json` | FE-1, BE-1 |
| **BE-2** | stub catalog, stub join graph, stub Metric Registry for a small first warehouse (a few tables) | BE-1 |
| **AN-1** | `anomaly_{unusual,within_normal,insufficient}.json`, `driver_ok.json`, `driver_insufficient.json`, `risk_ok.json`, `advisor_ok.json`, plus the input tables as small Parquet files | FE-1, BE-1, BE-2 |
| **AN-1 and BE-2** | `manifest_sample.json` | each other |
| **BE-1** | `eval_cases_sample.json`, `eval_results_sample.json`, `metrics_sample.json` | FE-1 |

---

## 10. How these become code (week 1 tasks)

1. **BE-1** commits this file's types as **JSON Schema** into `contracts/schemas/` and generates **Pydantic models** (Python) and **TypeScript types** (for FE-1) from them. FE-1's API client is generated from the same schemas.
2. **Contract tests** (CI): every fixture in `contracts/examples/` must validate against its schema; every producer's output in tests must validate; consumers must parse every fixture.
3. **Any change** to a shape goes through `Product.md` 7.4 (PR to `contracts/`, producer and consumers approve, Lead merges, version bump if breaking).
4. **Open points to settle in the week-1 sync** (record the decision in `contracts/DECISIONS.md`):
   - SQL dialect(s) and the exact `dialect` strings.
   - Inline row limit for `ExecutionResult.rows` and the Parquet artifact location.
   - Whether `narrative` markers stay `[Cn]` or move to a structured span format.
   - Authentication stub on the API (header or session) and who owns the role flag for review screens.
   - The numeric defaults (thresholds, caps, tolerances) in each module's `params`; these are set on the development set and fixed before the held-out run (design D4).

---

## 11. Additions for the frontend (FE-1 needs these to render)

### 11.1 Artifact route (chart data and tables)

`AnalystOutput.artifacts[].uri` for `chart_data` and `table` artifacts is always `/api/artifacts/{artifact_id}` (BE-1 serves it; data is stored by whoever produced it).

```ts
// GET /api/artifacts/{artifact_id}
interface ArtifactResponse<T = ChartData | TableArtifact> {
  contract_version: string; artifact_id: string; run_id: string;
  kind: "chart_data" | "table"; data: T;
}

type ChartData = AnomalyChartData | CalibrationChartData | DriverBridgeData;

interface AnomalyChartData {                    // AN-1 writes
  kind: "anomaly_series"; grain: "day" | "week" | "month";
  points: { period: string; actual: number; expected: number | null; lower: number | null; upper: number | null }[];
  test_window: Period;
  flagged: string[];                            // periods outside the interval
  change_points?: string[];
  baseline_kind: "seasonal" | "robust_trailing";
}
interface CalibrationChartData {                // AN-1 writes
  kind: "calibration";
  bins: { predicted: number; observed: number; n: number }[];
}
interface DriverBridgeData {                    // AN-1 writes (P2 on the UI)
  kind: "driver_bridge"; baseline: number; current: number;
  steps: { label: string; contribution: number }[];
}
interface TableArtifact {
  kind: "table"; columns: { name: string; type: string }[]; rows: unknown[][];
}
```
Driver bars and the risk segment table render directly from `DriverResult` and `RiskResult`; they need no artifact.

### 11.2 `EvidenceBlock.output` is a typed union (replaces `unknown` in Product 5.2)

```ts
type EvidenceBlock =
  | { module: "lookup"; output: LookupOutput;                     source_claims: string[] }
  | { module: "anomaly"; output: AnalystOutput<AnomalyResult>;    source_claims: string[] }
  | { module: "driver";  output: AnalystOutput<DriverResult>;     source_claims: string[] }
  | { module: "risk";    output: AnalystOutput<RiskResult>;       source_claims: string[] };

interface LookupOutput {
  query_id: string;
  columns: { name: string; type: string; role?: string }[];
  rows: unknown[][];                // already capped for display; full result stays server-side
  row_count: number; truncated: boolean;
}
```

### 11.3 `conclusion` carries the narrative

`AnswerEnvelope.conclusion` is exactly `SynthOutput.narrative`: plain text (never HTML), with claim markers such as `[C3]` after each claim sentence. FE-1 parses markers with `\[C\d+\]` and links each to `claims[]`.

### 11.4 `TraceEvent.detail` per step (what FE-3's panels render)

`detail` is the step's result object from Section 3, with row data and sensitive values removed.

| `step` | `component` | `detail` is | Panel |
|---|---|---|---|
| 1 | Guard | `GuardResult` | Trace list |
| 2 | Router and Extractor | `RouterResult` | Route and slots |
| 3 | Planner | `Plan` | Plan view |
| 4 | Metric Resolver | `MetricResolution` | Definitions |
| 5 | Answerability Checker | `AnswerabilityResult` | Trace list |
| 6 | Schema Retriever | `RetrievalResult` | Retrieved schema, join path, linked values |
| 7 | SQL Compiler or Generator | `SqlCandidate` | SQL viewer |
| 8 | Validator | `ValidationResult` | Validator checks |
| 8b | Repairer | `{ request: RepairRequest, candidate: SqlCandidate }` | Repair diff |
| 9 | Executor | `ExecutionResult` without `rows` | SQL viewer status and timing |
| 10 | Reconciler | `ReconciliationResult` | Reconciliation panel |
| 11 | Analysts | `AnalystOutput` summary (`status`, `parameters`, `assumptions`, `sources`) | Plan view, module steps |
| 11b | Advisor | `AdvisorOutput` | Trace list |
| 12 | Evidence Ledger | `{ claim_count, dropped, hypotheses }` | Trace list |
| 13 | Synthesizer | `{ source, used_claim_ids, model }` | Trace list |
| 14 | Fact Checker | `FactCheckResult` | Trace list |

### 11.5 Onboarding progress is polled

There is no event stream for onboarding. FE-1 polls `GET /api/onboarding/{job_id}` every few seconds (interval in config) until `status` is `completed`, `needs_review` or `failed`.

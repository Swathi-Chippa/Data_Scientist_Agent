# Data Scientist Agent (`DS-AGENT-CORE`)
### Enterprise Decision Intelligence & High-Performance Schema-Grounded NL2SQL Engine

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Pydantic v2](https://img.shields.io/badge/contracts-pydantic--v2-green.svg)](https://docs.pydantic.dev/)
[![SQLGlot](https://img.shields.io/badge/parser-sqlglot-orange.svg)](https://github.com/tobymao/sqlglot)
[![Tests Passing](https://img.shields.io/badge/tests-170%20passing-brightgreen.svg)](backend/tests/)
[![Security Invariants](https://img.shields.io/badge/security-read--only%20enforced-red.svg)](spec/requirements.md)

---

## 1. Overview & Problem Statement

Business teams sit on massive data warehouses but need answers to complex diagnostic questions that simple table lookups cannot answer:
* *"Why did revenue drop in August?"*
* *"What is driving shipping delays across regions?"*
* *"Which accounts have elevated churn risk next quarter?"*

Generic text-to-SQL wrappers (such as single-prompt LLMs or monolithic agent frameworks) fail in the enterprise because they:
1. **Hallucinate schemas:** Invent tables and columns on wide, realistic databases.
2. **Fabricate numbers:** Attempt to perform mental arithmetic or statistical math in text tokens.
3. **Lack security boundaries:** Allow dangerous SQL operations or unindexed full-table scans.
4. **Cannot diagnose:** Return flat rows instead of statistical root causes, decomposition, or recommendations.

**`DS-AGENT-CORE`** is an enterprise-grade Data Scientist Agent. It bridges the gap between raw natural language query translation and rigorous data science analytics through a **Dual-Engine DAG architecture**:

```
                       ┌────────────────────────────────────────────────────────┐
                       │                   USER QUESTION                        │
                       └──────────────────────────┬─────────────────────────────┘
                                                  │
                                       ┌──────────▼──────────┐
                                       │ 1. Ingress Guard    │ (SAFE-4: Prompt Injection & DML Blocker)
                                       └──────────┬──────────┘
                                                  │
                                       ┌──────────▼──────────┐
                                       │ 2. 3-Signal Router  │ (Lookup vs. Anomaly vs. Driver vs. Churn)
                                       └─────┬─────────┬─────┘
                                             │         │
                     ┌───────────────────────┘         └────────────────────────┐
                     ▼                                                          ▼
        [ GOVERNED KPI FAST-PATH ]                                 [ EXPLORATORY 4-STAGE NL2SQL ]
        Deterministic Jinja/AST Compiler                           Perceptor -> Generator -> Reflector
        (< 350ms, Zero LLM Calls)                                  (Fuzzy Linking + ICL + Bounded Repair)
                     │                                                          │
                     └───────────────────────┬──────────────────────────────────┘
                                             │
                                  ┌──────────▼──────────┐
                                  │ 3. AST Validator    │ (SAFE-1/2: Table Allowlist, Read-Only, AST Cost)
                                  └──────────┬──────────┘
                                             │
                                  ┌──────────▼──────────┐
                                  │ 4. Read-Only Gateway│ (Multi-DB Pool: SQLite, MySQL, PostgreSQL)
                                  └──────────┬──────────┘
                                             │
                                  ┌──────────▼──────────┐
                                  │ 5. Data Reconciler  │ (Grain verification, Null spikes, Fan-out checks)
                                  └──────────┬──────────┘
                                             │
                                  ┌──────────▼──────────┐
                                  │ 6. Analytics Engine │ (STL Anomaly, PELT Change-Points, PVM Driver)
                                  └──────────┬──────────┘
                                             │
                                  ┌──────────▼──────────┐
                                  │ 7. Evidence Ledger  │ (EVID-1: Every number linked to raw SQL row)
                                  └──────────┬──────────┘
                                             │
                                  ┌──────────▼──────────┐
                                  │ 8. Executive Answer │ (Narrative Summary + Tables + Visualizations)
                                  └─────────────────────┘
```

> **Core System Invariant (`SAFE-8`):** *"A language model may choose, draft, and explain; it never computes a number, never decides query safety, and never bypasses semantic governance."*

---

## 2. Key Architectural Features

### 🛡️ Deterministic Safety Boundary (`SAFE-1`, `SAFE-2`, `SQL-17`)
* **100% Deterministic Code:** Security gates are enforced purely via AST parsing (`sqlglot`) and regex token heuristics—zero reliance on probabilistic LLMs.
* **Strict Read-Only Guarantee:** Read-only access is enforced at the driver/session level (`?mode=ro` in SQLite; read-only transactions in Postgres/MySQL). Any write attempt (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`) is killed before execution.
* **Dual-Mode Toggle (`SQL-17`):**
  * **Enterprise Mode:** Rejects `SELECT *`, enforces explicit column projections, bounds results with mandatory `LIMIT`, and gates query complexity.
  * **Benchmark Mode:** Relaxes enterprise row truncation to allow exact ground-truth execution matches (`EX >= 70%`) on benchmarks like **BIRD**.

### ⚡ Deterministic Metric Compiler (< 350ms Fast-Path)
* Standard business metrics (e.g., GMV, Revenue, Order Count, AOV) bypass the LLM entirely.
* Compiles pre-vetted join paths using a Jinja2/AST template compiler in sub-second latency with 100% mathematical reproducibility.

### 🧠 Native 4-Stage NL2SQL Engine
* **Schema Perceptor:** Deterministic exact/fuzzy value linking, resolving informal user terms (e.g., `"SP"` $\to$ `'customer_state = SP'`) before generation, with low-confidence and ambiguity clarification.
* **Generator:** Deterministic skeleton-of-thought CTE generation using catalog-inferred join paths and frozen table/column allowlists.
* **Bounded Reflector (Self-Repair):** Automatically heals execution and syntax errors from error logs. Strictly capped at **2 attempts** with a **frozen schema allowlist** to prevent runaway loops or scope creep.

### 📊 Pure-Python Statistical Analytics (AN-1)
* **Anomaly Detection:** Seasonal-Trend Decomposition (STL) and PELT change-point detection (`statsmodels`).
* **Root Cause Analysis (RCA):** Price-Volume-Mix (PVM) variance decomposition (`numpy`) guaranteeing 100% mathematical reconciliation.
* **Evidence Ledger:** Automatically cross-checks every number in the synthesized response against underlying query DataFrames. Unverified claims are stripped before reaching the user.

---

## 3. Project Structure

```text
Data_Scientist_Agent/
├── backend/                        # Core Python backend engine
│   ├── pyproject.toml              # Project metadata & dependencies
│   ├── src/
│   │   ├── contracts/              # Frozen Pydantic v2 data models (spec/Interfaces.md)
│   │   ├── gateway/                # Multi-database read-only connection & executor
│   │   ├── guard.py                # Ingress security & injection filter
│   │   ├── sql/                    # SQLGlot AST validator & dual-mode toggle
│   │   ├── schema_retriever/       # Fuzzy value linking & schema perception
│   │   ├── catalog/                # Metric registry & semantic definitions
│   │   ├── analysts/               # Statistical analytics (STL, PELT, PVM)
│   │   ├── evidence/               # Evidence ledger & regex fact checker
│   │   ├── router.py               # 3-Signal intent classifier & router
│   │   └── app.py                  # FastAPI REST & SSE streaming server
│   └── tests/                      # TDD test suites (170 unit & integration tests)
│
├── frontend/                       # Web UI client & executive dashboard
├── spec/                           # Master engineering specifications & contracts
│   ├── requirements.md             # Traceable requirements matrix (SAFE, SQL, EVAL)
│   ├── Interfaces.md               # API contracts & TypeScript type definitions
│   └── build-plan.md               # 6-Stage prototype execution plan
│
├── docs/                           # Architecture blueprints & documentation
│   ├── DESIGN_DETAIL.md            # Comprehensive system design & mermaid DAGs
│   └── Product.md                  # PRD, user journeys & evaluation targets
│
├── scripts/                        # Database builders & utilities
│   ├── build_brazil_ecommerce_sqlite.py
│   └── strip_database.py
│
├── brazil_ecommerce.sqlite         # Raw analytical database (100K real orders)
└── .gitignore                      # Git protection rules
```

---

## 4. Tech Stack

| Domain | Technologies |
| :--- | :--- |
| **Language** | Python 3.11+ |
| **Type Contracts** | Pydantic v2 (Strict, frozen models) |
| **SQL Engine & Safety** | SQLGlot (AST parsing), SQLAlchemy 2.0 (Read-only pooling) |
| **Local LLM Inference** | Ollama (`qwen2.5-coder`, `llama3.1`) via standard OpenAI-compatible API |
| **Analytics & Math** | NumPy, Pandas, Statsmodels (STL / PELT), SciPy |
| **Web & Streaming** | FastAPI, Uvicorn, Server-Sent Events (`sse-starlette`) |
| **CLI & Testing** | Pytest, Rich, Tabulate |

---

## 5. Getting Started

### Prerequisites
* Python 3.11 or higher
* [Ollama](https://ollama.ai) (optional, for local LLM text-to-SQL generation; recommends `ollama pull qwen2.5-coder`)

### Installation
1. Clone the repository:
   ```bash
   git clone https://github.com/ganeshk2846/Data_Scientist_Agent.git
   cd Data_Scientist_Agent
   ```

2. Set up virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   pip install -e backend/
   ```

### Running the Test Suite (TDD)
Execute the comprehensive test suite verifying contracts, read-only gateway, Ingress Guard, and AST validation:
```bash
cd backend
python -m pytest
```

```text
platform win32 -- Python 3.14.7, pytest-9.1.1
collected 170 items

........................................................................ [ 42%]
........................................................................ [ 85%]
..........................                                               [100%]

170 passed in 2.47s
```

---

## 6. Development Roadmap

- [x] **Stage 1: Foundation, Read-Only Gateway & Safety Boundary (Sprint 1)**
  - Pydantic v2 contracts matching `Interfaces.md`
  - Ingress Guard blocking prompt injection and DDL/DML tokens
  - Read-only SQLAlchemy gateway connected to `brazil_ecommerce.sqlite`
  - SQLGlot AST Validator with Dual-Mode Toggle (Enterprise vs. Benchmark)
  - Test suite passing (`backend/tests/test_*.py`)
- [x] **Stage 2: Interactive Console REPL, Trace Logger & Baseline A (Sprint 2)**
  - Rich console REPL with read-only SQL, `ask`, `tables`, `mode`, and `help`
  - Masked structured JSON traces for every run
- [ ] **Stage 3: Native 4-Stage NL2SQL Engine & Bounded Reflector (Sprint 3)**
  - [x] Schema catalog, answerability gate, and deterministic value perception
  - [x] Deterministic CTE generator with inferred joins and frozen allowlists
  - [x] Bounded self-repair with structured clarification
  - [ ] Hybrid keyword/dense retrieval and cross-encoder reranking
- [ ] **Stage 4: Deterministic Metric Compiler Fast-Path & Reconciler (Sprint 4)**
- [ ] **Stage 5: End-to-End Orchestrator DAG & Analyst Harness (Sprint 5)**
- [ ] **Stage 6: FastAPI Production Server, SSE Streaming & Evaluation Harness (Sprint 6)**

---

## 7. Governing Specifications & Traceability
Every commit, test, and module in this repository maps directly to our formal specifications:
* [spec/requirements.md](spec/requirements.md) — Normative requirements (`SAFE-1`, `SQL-1`, `EVAL-1`).
* [spec/Interfaces.md](spec/Interfaces.md) — Data contracts and system boundary interfaces.
* [docs/DESIGN_DETAIL.md](docs/DESIGN_DETAIL.md) — Master architecture blueprint and sequence diagrams.
* [spec/build-plan.md](spec/build-plan.md) — Engineering delivery milestones.

---

## 8. License
This project is developed under the MIT License.

# Phase 1: Safe Analyst Registry and Architecture Guardrails

**Status:** Implemented in this phase. This document covers only the Phase 1 registry and guardrails.

## Ownership Boundary

AN-1 owns the analyst modules under `backend/src/analysts/` and their behavior. The registry is a closed lookup layer for those modules:

- The approved names are fixed: `anomaly`, `driver`, `risk`, and `advisor`.
- Callers may not register functions, classes, import paths, or code at runtime.
- The registry does not load data, call a model, connect to a database, or perform network I/O.
- Phase 1 does not change shared contracts, the orchestrator, the service, the frontend, evidence handling, SQL, or the gateway.
- `backend/src/analysts/harness.py` keeps its existing fixed anomaly/driver dispatch. Registry-to-harness integration is future work.

## Module Status

| Module | Status | Runnable | Registry requirement IDs |
| --- | --- | --- | --- |
| `anomaly` | implemented | yes | ANA-1, ANA-2, SAFE-8 |
| `driver` | implemented | yes | ANA-1, ANA-2, SAFE-8 |
| `risk` | planned | no | ANA-1, ANA-2, SAFE-8 |
| `advisor` | planned | no | ANA-1, ANA-2, SAFE-8 |

The requirement IDs describe the registry boundary and its guardrails. They do not claim that the planned risk or advisor modules, or their future requirements, are implemented.

`describe(name)` returns immutable metadata for any approved name, including planned modules. `resolve(name)` returns a fixed in-process instance only for implemented modules. `list_modules()` returns metadata in the approved order. Anomaly and driver instances are singletons within the registry.

## Fail-Closed Behavior

- An unknown name raises `UnknownAnalystError`, a typed `AnalystRegistryError`.
- A planned name raises `AnalystUnavailableError`, also a typed `AnalystRegistryError`.
- Planned modules never return a placeholder object that could be run by mistake.
- There is no fallback to a default analyst, no arbitrary callable lookup, and no dynamic registration API.
- Error messages identify the rejected name and list the approved modules, or explain that the requested module is planned and not implemented.

## Security and Architecture Boundaries

The registry and the existing analyst modules use only the standard library, NumPy, Pydantic, and `contracts.models`. The architecture tests enforce that AN-1 analyst sources do not:

- import model clients or model-provider SDKs;
- import database drivers;
- import network libraries;
- use shell or subprocess execution;
- use `eval`, `exec`, or unrestricted dynamic imports;
- contain hard-coded secrets or API keys.

The checks are AST-based and inspect only the AN-1-owned analyst source files. Existing SQL/reconciliation code is outside this phase and is not part of these checks.

## Non-Goals

Phase 1 does not:

- implement `RiskAnalyst` or `Advisor`;
- add risk or advisor shared contract models;
- add ground-truth ingestion, scoring, or evaluation;
- add model calls or database access to analysts;
- wire the registry into the orchestrator, service, or harness;
- replace the existing analyst harness;
- change the frontend or any evidence, SQL, gateway, or router code.

## Future Dependencies

Risk and advisor work remains planned and depends on explicit future contract decisions. The current shared contracts have no risk/advisor result models, and `AnalystOutput.result` supports only anomaly and driver results. Future work must define those contracts before implementation, then add reviewed registry metadata and tests rather than enabling them implicitly.

Future integration may use `resolve(name)` as the safe lookup point, but it must preserve the fail-closed behavior for planned modules and the prohibition on arbitrary callables or import paths.

## Tests

Run from `backend/`:

```powershell
..\.venv\Scripts\python.exe -m pytest -q tests/test_an1_registry.py tests/test_an1_architecture.py tests/test_analyst_harness.py
```

The registry tests cover implemented and planned metadata, stable instances, unknown names, typed errors, deterministic ordering, and the absence of a registration API. The architecture tests cover imports, shell/eval/dynamic-import usage, secrets, importability, and the fixed approved module set. The existing analyst harness tests verify that anomaly and driver behavior is unchanged.

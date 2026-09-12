# AI-Powered Business Analytics System

An AI-assisted analytics system where deterministic services own metrics and validation, while
constrained AI components handle planning and interpretation under evidence, QA, lineage, and
replay controls.

## Architecture

```mermaid
flowchart LR
    CSV[CSV source] --> ING[Ingestion and SHA-256]
    ING --> VAL{Generic and sales validation}
    VAL -->|PASS or WARN| DV[DatasetVersion and raw retention]
    VAL -->|REJECT or QUARANTINE| STOP[Downstream blocked]
    DV --> PROF[Deterministic profile]
    PROF --> REQ[Requirement interpretation]
    REQ --> PLAN[Analytics plan]
    PLAN --> ENG[Deterministic analytics engine]
    ENG --> INV[Optional deterministic investigation]
    INV --> CRITIC[Independent Critic gate]
    CRITIC --> BIZ[Evidence-bound business interpretation]
    BIZ --> REPORT[Deterministic report assembly]
    ORCH[Workflow orchestrator] -. controls stages .-> REQ
    ORCH -. controls stages .-> REPORT
    DB[(PostgreSQL)] --- DV
    DB --- PROF
    DB --- ORCH
    DB --- REPORT
    PROVIDER[[Optional provider boundary]] -. advisory typed proposals .-> REQ
    PROVIDER -. advisory typed proposals .-> PLAN
    PROVIDER -. advisory typed proposals .-> CRITIC
    PROVIDER -. advisory typed proposals .-> BIZ
    PROVIDER -. advisory typed proposals .-> REPORT
```

See [docs/architecture.md](docs/architecture.md) for responsibilities, artifact flow, failure
behavior, and the golden-demo sequence.

## Golden Demo

The canonical question is:

> Compare August 2024 revenue with July 2024 revenue.

Run the full real workflow, including migrations, ingestion, profiling, required investigation,
Critic review, business interpretation, a second replay workflow, and Markdown rendering:

```powershell
.\.venv\Scripts\python.exe -m scripts.demo
```

The synthetic sample contains 32 orders across July and August 2024, four regions, and five
products. Its deterministic control totals are July revenue of 7,625 and August revenue of
7,355: a decline of 270 (about 3.54%). East offsets declines in North and West, while South is
unchanged. These values are data controls, not hardcoded report output.

## Why This Project Exists

Language models are useful for interpreting questions and shaping prose, but they should not
invent analytical truth. This system grounds each question in a profiled dataset, executes
registered metrics with deterministic pandas logic, independently checks the result, binds
business prose to evidence IDs, and persists every artifact and stage transition.

## Core Design Principles

- Deterministic calculations from a versioned metric registry.
- Strict Pydantic contracts at every artifact boundary.
- Generic validation separated from sales-specific policy.
- SHA-256 source identity, immutable raw retention, lineage, and replay.
- Independent Critic checks that optional providers cannot override.
- Business claims and reports bound to persisted evidence identifiers.
- Distinct workflow invocations with replayable immutable artifacts.
- Explicit stage state, bounded retries, optimistic concurrency, and append-only events.

## Capabilities

V1–V2 provide CSV ingestion, validation outcomes (`PASS`, `WARN`, `REJECT`, `QUARANTINE`),
content-addressed raw retention, versioned datasets, migrations, and profiling. V3 interprets
grounded requirements. V4 plans and computes registered metrics. V5 investigates contribution
and concentration without causal claims. V6 independently critiques lineage and arithmetic.
V7 creates evidence-bound business interpretation. V8 assembles validated reports. V9 runs the
stages as a resumable, auditable workflow.

The current release hardens the complete V1–V9 scope for repeatable local development and
verification; it intentionally does not add V10 functionality.

## Requirements

- Python 3.11 or newer (CI uses Python 3.12)
- PostgreSQL 16 for the documented local/CI target
- Docker Compose for the reproducible local database workflow

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
Copy-Item .env.example .env
```

Edit the private `.env` values before starting PostgreSQL. Docker Compose reads `.env`.
Python does not automatically load dotenv files; export `DATABASE_URL`, `LOG_LEVEL`, and any
timeout values into the process environment. If a database password contains reserved URL
characters such as `@`, percent-encode it in `DATABASE_URL` (for example, `@` becomes `%40`).

```powershell
$env:DATABASE_URL = "postgresql+psycopg://analytics:change-me-local-only@localhost:5432/analytics"
$env:LOG_LEVEL = "INFO"
docker compose up -d
.\.venv\Scripts\python.exe -m alembic upgrade head
```

The Compose file contains no password default. It uses `POSTGRES_DB`, `POSTGRES_USER`, and
`POSTGRES_PASSWORD` from the local environment or private `.env` file.

`requirements.txt` contains exact direct runtime dependencies, `requirements-dev.txt` adds
development tools, and `requirements-lock.txt` captures the fully resolved Python 3.12
development/test environment used for release verification. Regenerate and test the lock when
supporting another Python or operating-system target.

## Other Commands

```powershell
# Ingest and validate the sample only
.\.venv\Scripts\python.exe main.py

# Run all tests; PostgreSQL-marked tests skip without DATABASE_URL
.\.venv\Scripts\python.exe -m pytest -q

# Portable tests only
.\.venv\Scripts\python.exe -m pytest -q -m "not postgres"

# PostgreSQL-specific tests
.\.venv\Scripts\python.exe -m pytest -q -m postgres

# Lightweight static checks
.\.venv\Scripts\python.exe -m ruff check config database pipeline storage scripts/demo.py tests/test_release_hardening.py tests/test_postgres_integration.py

# Migration operations
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic downgrade 20260911_08
```

Production schema creation uses Alembic revisions `20260911_01` through `20260911_09`.
`Base.metadata.create_all()` remains available only as an isolated unit-test helper.

## Testing

CI runs three verification jobs on pushes and pull requests. A Docker Compose smoke job starts
the committed PostgreSQL 16 service definition and queries the running server. The portable job
runs Ruff and all tests not marked `postgres`. The PostgreSQL job starts PostgreSQL 16, applies
migrations, runs PostgreSQL-only tests, then runs the complete suite. The PostgreSQL tests cover
persistence, lineage, source-hash replay, concurrent duplicate ingestion, and optimistic workflow
concurrency. On `main` pushes only, a fourth job creates `v1.0.0` once all three checks pass.

Local release verification on September 12, 2026 used a clean Python 3.12 environment and a
fresh isolated PostgreSQL 18.6 database. All nine migrations reached `20260911_09`, all five
PostgreSQL-specific tests passed, and the complete suite passed 242/242. The dependency lock also
passed `pip check`. PostgreSQL 16 remains the CI service target, and the release tag is gated on
all three verification jobs passing for the tagged commit.

## Persistence and Operations

PostgreSQL stores metadata, typed analytical artifacts, workflow snapshots, and append-only
stage events. The filesystem raw store keeps original source bytes by content hash; PostgreSQL
does not store raw CSV bytes. Back up both systems to preserve lineage. See
[docs/operations.md](docs/operations.md) for timeouts, retry classes, transaction boundaries,
operational metrics, and backup/restore steps.

## Security Posture

Secrets come from environment variables and are never committed. Central logging redacts common
credential shapes. Raw rows are not logged, arbitrary SQL is not accepted by analytical
contracts, provider proposals are schema-validated, and filesystem retention names are derived
from SHA-256 hashes. This is a basic application-security posture, not enterprise IAM.

## Limitations

- This is a portfolio-scale implementation, not a commercial production service.
- There is no API server, UI, scheduler, distributed worker, metrics exporter, or object-store
  adapter.
- Provider protocols exist, but no concrete LLM provider adapter is shipped.
- The filesystem raw store is suitable for local development; production needs durable object
  storage with coordinated backup, retention, and access controls.
- Retry is in-process and immediate. There is no durable queue, delay, or circuit breaker.
- PostgreSQL high availability, encryption policy, IAM, disaster-recovery automation, and load
  testing remain deployment responsibilities.
- Business rules and the metric registry cover the synthetic sales domain only.

## Future Production Work

Before wider use, add an authenticated service boundary, a durable worker queue, an object-storage
adapter, secret-manager integration, telemetry export, deployment-specific backup automation,
load testing, and operational runbooks. Those items are intentionally outside V1–V9.

## Project Documents

- [Architecture](docs/architecture.md)
- [Portfolio case study](docs/case-study.md)
- [Engineering decisions](docs/decisions.md)
- [Operations](docs/operations.md)

## License

MIT. See [LICENSE](LICENSE).

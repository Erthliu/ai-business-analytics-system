# Operations

## Persistence Inventory

Production state spans two coordinated stores:

1. PostgreSQL contains pipeline runs, dataset-version metadata, lineage, aggregate profiles,
   typed analytical artifacts, workflow snapshots, and append-only workflow events.
2. Raw storage contains immutable original CSV bytes addressed by SHA-256. The included adapter is
   filesystem-backed. PostgreSQL stores its URI and hash, not the raw file.

Losing either store breaks complete lineage. Back up and restore them to the same logical point.

## Local PostgreSQL

Copy `.env.example` to a private `.env`, change the local password, and ensure `DATABASE_URL`
contains its percent-encoded form. Then:

```powershell
docker compose up -d
$env:DATABASE_URL = "postgresql+psycopg://analytics:<encoded-password>@localhost:5432/analytics"
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m pytest -q -m postgres
```

Stop the service with `docker compose stop`. `docker compose down` removes containers but keeps the
named volume. `docker compose down -v` destroys the local database and should only be used when a
disposable reset is intended.

## Migrations

Alembic is the production schema mechanism. Revisions `20260911_01` through `20260911_09` form a
linear chain. Review migration SQL and downgrade behavior before deployment. A release should run
`alembic current`, take a backup, run `alembic upgrade head`, then verify the reported head.
`create_all()` is reserved for isolated tests.

## Timeout and Retry Policy

Database connection establishment has an explicit timeout (`DATABASE_CONNECT_TIMEOUT_SECONDS`,
default 10 seconds) and pool connections are pre-pinged. Optional provider implementations must
enforce `PROVIDER_TIMEOUT_SECONDS` (default 30 seconds); this repository ships no adapter and
therefore performs no external provider calls.

Workflow retries are immediate, in-process, and bounded. Only SQLAlchemy `OperationalError`, OS
I/O errors, and timeouts are classified as transient. Contract, semantic, validation, Critic, and
lineage failures are not retryable. Production workers should add delayed retries and circuit
breaking outside the domain workflow rather than widening these classifications.

## Transaction Boundaries

- Pipeline run creation is committed before ingestion so an attempt has durable identity.
- Dataset version creation, run completion, and lineage are separate intentional boundaries. A
  failure is recorded on the run; raw content remains immutable and safe to retry operationally.
- Each immutable artifact store commits one artifact. Insert races roll back and load the unique
  winner.
- A workflow snapshot update and its stage events commit together using optimistic concurrency.
- The whole workflow is deliberately not one transaction; long analytics work does not hold
  database locks.

An artifact can commit before the workflow points to it. Resume safely finds and replays the
artifact by identity. A workflow event cannot commit without its corresponding snapshot update.

## Operational Metrics to Export

The application logs the identifiers and durations needed to derive these metrics. A production
deployment should export them without adding a metrics server to the domain package:

| Metric | Dimensions |
| --- | --- |
| Workflow count and duration | status, audience |
| Stage duration and failure count | stage, status, error code, retryable |
| Blocked workflow count | stage, Critic severity |
| Clarification rate | requirement rules version |
| Artifact replay rate | stage, artifact type |
| Investigation rate | policy, routing reason |
| Critic failure rate | severity, QA rules version |
| Provider failure/timeout rate | provider, model, stage |
| Database failure rate | operation, transient classification |

Never use raw rows, questions containing PII, credentials, or provider keys as metric labels.

## Backup and Restore

Example logical PostgreSQL backup:

```powershell
pg_dump --format=custom --file=analytics.dump analytics
```

At the same maintenance point, snapshot or copy the configured raw-storage root while preserving
filenames and bytes. Record database backup time, raw snapshot identity, application version, and
Alembic revision together.

Restore into an empty database:

```powershell
createdb analytics_restore
pg_restore --dbname=analytics_restore --clean --if-exists analytics.dump
```

Restore the matching raw snapshot to its configured root, set `DATABASE_URL`, run `alembic
current`, and verify that sampled `storage_uri` files exist and match their stored SHA-256 hashes.
Test this process regularly on non-production infrastructure. Do not assume an untested backup is
recoverable.

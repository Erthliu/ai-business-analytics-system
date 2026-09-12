# Engineering Decisions

## Deterministic metric execution

The LLM/provider boundary may propose an interpretation or layout, but it never calculates a
metric. Registered pandas operations are reproducible and independently testable.

## Semantic metric registry

Named metrics define their source fields, aggregation, additivity, and valid operations. This
prevents a fluent question from silently changing the mathematical definition.

## No causal claims in investigation

V5 decomposes observed change and concentration. It reports contribution and association, not
causation, because the system has no experimental or causal-identification design.

## Critic is an independent hard gate

The Critic recalculates provenance and numerical invariants. Provider advice is advisory and
cannot turn a deterministic failure into a pass.

## Evidence-bound business prose

Every factual claim references persisted evidence. Unsupported numbers, causal language, and
prescriptive recommendations fail validation rather than being polished into a report.

## Distinct workflows, replayable artifacts

Repeated user invocations produce distinct workflow records for auditability. Immutable artifacts
reuse stable identity hashes so repeated work does not duplicate analytical truth.

## PostgreSQL schema through Alembic

Alembic provides reviewed, ordered production migrations. SQLAlchemy `create_all()` remains only
for fast isolated tests and is never the deployment mechanism.

## SQLite for portable tests, PostgreSQL for database evidence

SQLite keeps most contract and deterministic logic tests fast. PostgreSQL-specific migration,
constraint, transaction, and concurrency behavior is tested separately against PostgreSQL 16 in
CI. SQLite results are not represented as PostgreSQL verification.

## Filesystem raw storage as an adapter

The local adapter preserves source bytes by hash without putting them in PostgreSQL. Its protocol
allows a future object-store implementation without changing dataset identity or lineage.

## Small explicit retry taxonomy

Only clear infrastructure exceptions are retryable. Semantic errors are stable under retry and
must remain visible. Immediate retries are intentionally bounded to avoid loops and duplication.

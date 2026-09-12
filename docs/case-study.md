# Portfolio Case Study

## 1. Problem

Business users want to ask plain-language questions of tabular data. A useful system must do more
than produce plausible prose: it must preserve source identity, apply known metric definitions,
show evidence, detect bad inputs and calculations, and recover safely from interrupted work.

## 2. Why naive LLM analytics is unsafe

An unconstrained model can select the wrong field, confuse time periods, invent arithmetic,
overstate correlation as causation, or present unsupported advice. Fluency does not establish
correctness. This project treats provider output as an untrusted proposal at typed boundaries.

## 3. Architecture choices

The system uses small stage services connected by immutable Pydantic artifacts. PostgreSQL stores
metadata and artifacts; the raw CSV remains in content-addressed storage. Alembic owns schema
evolution. An orchestrator persists each stage boundary rather than hiding a long chain inside one
agent call.

## 4. Deterministic and AI separation

Ingestion, validation, profiling, planning validation, metric execution, investigation arithmetic,
QA, evidence binding, report validation, and rendering are deterministic. Optional providers can
suggest interpretations or presentation structure, but deterministic code regrounds and validates
every proposal. No provider adapter or LLM call is required for the golden path.

## 5. Metric semantic layer

The metric registry defines supported measures and operations. Plans reference known fields,
filters, periods, dimensions, and result shapes. This keeps “revenue” stable across scalar,
comparison, trend, ranking, and grouped analyses.

## 6. Investigation methodology

For period comparisons, the investigation stage decomposes total change by eligible dimensions,
checks reconciliation, identifies offsets, and measures concentration. It deliberately avoids
causal language and refuses unsupported metric shapes.

## 7. QA and Critic design

The Critic independently checks artifact linkage, source hashes, plan identity, arithmetic,
percentage-change policy, ordering, group reconciliation, and evidence references. A hard failure
blocks all downstream business interpretation and reporting.

## 8. Evidence-bound interpretation

Business claims carry evidence IDs. Validators reject unknown references, altered values, causal
claims, and recommendations that exceed the descriptive evidence. The report assembler renders
only validated sections, charts, tables, caveats, and provenance.

## 9. Orchestration

The V9 orchestrator advances one stage at a time, records status and attempts, appends events,
supports clarification and resume, routes optional investigation, and makes Critic approval a hard
gate. Workflow invocations stay distinct while immutable artifacts replay.

## 10. Reliability engineering

Source hashes, unique constraints, insert-race recovery, optimistic workflow concurrency, bounded
retry classification, explicit failure states, content-addressed raw retention, migrations, and
structured logs address the common ways a multi-stage analytics process becomes unreliable.

## 11. Testing

The suite covers contracts, semantic failure modes, deterministic calculations, migrations,
persistence, idempotency, lineage, retries, crash gaps, provider fallbacks, QA gates, reporting,
and orchestration. PostgreSQL-marked tests cover database-specific constraints and concurrency;
portable tests use SQLite where database semantics are not the subject.

## 12. Tradeoffs

The design has more artifacts and validation code than a single prompt pipeline. In exchange, it
makes calculation and failure behavior inspectable. It does not yet include a service layer,
distributed workers, durable object storage, or telemetry export, which keeps the portfolio scope
focused but limits production deployment.

## 13. What this demonstrates

The project demonstrates that AI-facing systems benefit from conventional data engineering:
schema control, deterministic cores, transactional boundaries, immutable evidence, independent
verification, and honest operational limits. The central lesson is that trust comes from the
system surrounding a model, not from model confidence alone.

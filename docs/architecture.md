# Architecture

## Scope

The repository implements a controlled V1–V9 analytics workflow. It accepts a versioned CSV and
a constrained business question, calculates registered metrics, optionally investigates drivers,
independently critiques the evidence, and renders a Markdown report. It does not expose an API or
run autonomously.

## Components

| Component | Responsibility | Boundary |
| --- | --- | --- |
| `pipeline` | Hash, ingest, validate, and register source data | Deterministic |
| `storage` | Retain immutable raw bytes by content hash | Deterministic I/O |
| `profiling` | Produce aggregate dataset metadata | Deterministic; optional provider enrichment |
| `agents/requirement_agent.py` | Ground a question in known fields, metrics, and dates | Deterministic default; typed provider proposal optional |
| `agents/analytics_planner.py` | Create a semantically valid execution plan | Deterministic default; typed provider proposal optional |
| `agents/analytics_engine.py` | Execute registered pandas calculations | Always deterministic |
| `investigation` | Decompose changes and concentration without causal claims | Always deterministic |
| `qa` and `agents/critic_agent.py` | Recheck provenance, arithmetic, coverage, and evidence | Deterministic gate; provider advice cannot override |
| `business` | Bind factual prose to evidence IDs | Deterministic fallback; constrained provider wording optional |
| `reporting` | Validate layout specifications and render Markdown | Deterministic facts and rendering |
| `orchestration` | Persist state, route stages, retry transient failures, resume safely | Deterministic control plane |
| `database` and `migrations` | Store lineage, artifacts, snapshots, and events | PostgreSQL production target |

## Artifact and Control Flow

```mermaid
flowchart TD
    subgraph deterministic[Deterministic trust boundary]
      CSV[CSV] --> HASH[SHA-256 and immutable raw copy]
      HASH --> VALIDATE{Validation outcome}
      VALIDATE -->|PASS or WARN| VERSION[DatasetVersion]
      VALIDATE -->|REJECT| REJECT[Persist and stop]
      VALIDATE -->|QUARANTINE| QUARANTINE[Persist and isolate]
      VERSION --> PROFILE[DatasetProfile]
      PROFILE --> REQUEST[AnalysisRequest]
      REQUEST --> PLAN[AnalysisPlan]
      PLAN --> ANALYSIS[ComputedAnalysis]
      ANALYSIS --> ROUTE{Investigation policy}
      ROUTE -->|run| INVESTIGATION[InvestigationResult]
      ROUTE -->|skip| CRITIC[CriticReview]
      INVESTIGATION --> CRITIC
      CRITIC -->|PASS or PASS_WITH_WARNINGS| INSIGHT[BusinessInsight]
      CRITIC -->|FAIL| BLOCK[Block workflow]
      INSIGHT --> SPEC[ReportSpec]
      SPEC --> REPORT[ReportArtifact and Markdown]
    end
    PROVIDER[[Optional external provider]] -. typed proposal only .-> REQUEST
    PROVIDER -. typed proposal only .-> PLAN
    PROVIDER -. advisory only .-> CRITIC
    PROVIDER -. constrained wording/layout .-> INSIGHT
    PROVIDER -. constrained wording/layout .-> SPEC
    ORCH[WorkflowOrchestrator] -. advances one persisted stage .-> REQUEST
    ORCH -. advances one persisted stage .-> REPORT
    DB[(PostgreSQL)] --- VERSION
    DB --- PROFILE
    DB --- REQUEST
    DB --- ANALYSIS
    DB --- CRITIC
    DB --- REPORT
    DB --- ORCH
```

Every durable artifact has its own identity and provenance. A new workflow invocation is always
distinct for auditability. Its immutable stage artifacts replay when their inputs and behavior
versions match.

## Validation Gates

- `PASS`: no detected issue; analytics may proceed.
- `WARN`: non-fatal quality issue; analytics may proceed with warnings.
- `REJECT`: fatal integrity issue; analytics stops.
- `QUARANTINE`: suspicious or policy-unknown input retained for review; analytics stops.

Generic checks cover emptiness, schema, duplicates, nulls, numeric types/ranges, and dates. The
sales policy separately checks accepted regions, positive measures, order-date range, and
`revenue = quantity × unit_price` within 0.01.

## Replay and Concurrency

Source bytes are keyed by SHA-256. A unique source hash prevents duplicate pipeline runs and raw
copies. Analytical stores use versioned identity hashes and database unique constraints. They
recover from insert races by rolling back and reading the winning immutable artifact.

Workflow snapshots use compare-and-swap updates on an integer version. The snapshot update and
its stage events commit in one database transaction. A stale writer raises
`StaleWorkflowVersionError`; it cannot overwrite the winner.

## Failure Behavior

The orchestrator marks semantic and integrity failures non-retryable. Only `OperationalError`,
`OSError`, and `TimeoutError` are retryable, and retry attempts are bounded. A persisted artifact
created just before a workflow snapshot failure is safe: resume recomputes its identity, replays
that immutable artifact, and then advances the snapshot. The Critic `FAIL` state blocks business
interpretation and reporting.

## Golden Demo Sequence

```mermaid
sequenceDiagram
    actor User
    participant O as Orchestrator
    participant R as Requirement
    participant A as Analytics
    participant I as Investigation
    participant C as Critic
    participant B as Business
    participant P as Report
    participant DB as PostgreSQL

    User->>O: Compare August 2024 revenue with July 2024 revenue
    O->>DB: create distinct WorkflowRun + event
    O->>R: interpret grounded requirement
    R->>DB: persist/replay AnalysisRequest
    O->>A: plan and calculate registered revenue
    A->>DB: persist/replay AnalysisPlan + ComputedAnalysis
    O->>I: route required investigation
    I->>DB: persist/replay plan + decomposition
    O->>C: independently verify lineage and numbers
    C->>DB: persist/replay CriticReview
    alt Critic passes
      O->>B: produce evidence-bound interpretation
      B->>DB: persist/replay BusinessInsight
      O->>P: validate specification and render Markdown
      P->>DB: persist/replay ReportSpec + ReportArtifact
      O->>DB: atomically complete workflow + append events
      O-->>User: validated Markdown report
    else Critic fails
      O->>DB: atomically block workflow + append event
      O-->>User: diagnostic failure, no report
    end
    User->>O: run the same question again
    O->>DB: create a new WorkflowRun
    O->>DB: replay matching immutable stage artifacts
```

# Storage Architecture

**Version:** 1.0

**Status:** Stable

**Last updated:** August 2026

---

# 1. Purpose

The Human Connection Protocol (HCP) defines the structure and semantics of Humanitarian Records.

It does **not** define:

- a database technology;
- a persistence engine;
- an indexing strategy;
- a storage provider.

Those concerns belong exclusively to the implementation.

The objective of the storage architecture is to allow HCP to operate over different persistence technologies while preserving identical protocol behavior.

The protocol must remain independent from infrastructure.

---

# 2. Design Principles

The storage layer follows six fundamental principles.

## Protocol Independence

HCP must never depend on a specific storage technology.

Whether records are stored in JSON, PostgreSQL or any future implementation, protocol behavior must remain identical.

---

## Separation of Responsibilities

Each layer owns a single responsibility.

Storage retrieves records.

Services interpret records.

Correlation builds Humanitarian Cases.

Presentation formats API responses.

No layer should assume responsibilities belonging to another layer.

---

## Infrastructure Agnostic

Infrastructure exists to support HCP.

HCP does not exist to support infrastructure.

Storage implementations may evolve independently without requiring protocol modifications.

---

## Offline Compatibility

Small nodes should be able to operate without requiring a relational database.

JSON storage remains a first-class implementation for:

- local development;
- testing;
- educational examples;
- offline deployments;
- lightweight humanitarian nodes.

---

## Testability

Business logic must be testable independently from the persistence engine.

Search algorithms should not require a running PostgreSQL instance.

Storage implementations should be replaceable by lightweight test doubles.

---

## Incremental Evolution

The architecture should allow new storage implementations without changing application services.

Future storage engines should only implement the RecordStorage contract.

---

# 3. Layered Architecture

```
                FastAPI
                    │
                    ▼
            RecordService
                    │
                    ▼
            SearchService
                    │
                    ▼
             RecordStorage
             /           \
            ▼             ▼
 JSONRecordStorage   PostgresRecordStorage
```

Application services never communicate directly with a database.

Every persistence implementation is accessed through RecordStorage.

---

# 4. RecordStorage

RecordStorage defines the storage contract used by the application layer.

Application services depend only on this interface.

They do not know:

- JSON;
- PostgreSQL;
- SQLAlchemy;
- Supabase;
- file systems;
- cloud providers.

Storage implementations may evolve independently provided they preserve this contract.

---

# 5. Storage Implementations

## JSONRecordStorage

JSON remains the reference implementation.

Its objectives are:

- local development;
- deterministic testing;
- offline execution;
- educational examples;
- protocol validation.

JSON prioritizes simplicity over scalability.

Search operations may inspect the complete collection because node sizes are expected to remain small.

---

## PostgresRecordStorage

PostgreSQL is the production-oriented implementation.

Its objectives are:

- efficient persistence;
- indexed candidate retrieval;
- transactional guarantees;
- scalability;
- production deployment.

PostgreSQL optimizes storage access without modifying protocol semantics.

---

# 6. Candidate Search

Candidate Search was introduced to avoid scanning the complete storage during every search.

The objective is **not** to calculate compatibility.

Its objective is to reduce the search space.

```
HumanitarianQuery

        │

        ▼

RecordStorage.search_candidates()

        │

        ▼

Candidate Records

        │

        ▼

SearchService

        │

        ▼

CorrelationService

        │

        ▼

HumanitarianCase
```

Storage implementations may use inexpensive structural filters such as:

- Subject Type
- Country
- Administrative Region
- Indexed timestamps

Candidate Search never determines whether two observations belong to the same Humanitarian Case.

---

# 7. Responsibilities

| Component | Responsibility |
|------------|----------------|
| RecordStorage | Retrieve candidate records |
| SearchService | Evaluate semantic compatibility |
| CorrelationService | Correlate observations |
| HumanitarianCaseBuilder | Produce API responses |

Maintaining these responsibilities independently simplifies testing, maintenance and future evolution.

---

# 8. Why the Search Algorithm Lives in SearchService

The search algorithm is part of the HCP domain model.

It is **not** part of the storage layer.

Search compatibility depends on protocol semantics rather than persistence technology.

Moving the algorithm into SQL would:

- duplicate business logic;
- increase implementation complexity;
- couple HCP to PostgreSQL;
- make future storage engines harder to support.

For this reason, PostgreSQL retrieves candidates.

SearchService evaluates candidates.

This separation preserves protocol independence.

---

# 9. Storage Independence

The RecordStorage abstraction allows future implementations such as:

```
RecordStorage

    │

    ├── JSON

    ├── PostgreSQL

    ├── SQLite

    ├── DuckDB

    ├── OpenSearch

    ├── Elasticsearch

    └── Remote HCP Node
```

Application services should not require modifications when introducing a new storage backend.

---

# 10. Runtime Configuration

Search candidate retrieval is controlled through SearchSettings.

Current parameters include:

- candidate_fetch_limit
- candidate_multiplier
- max_candidate_fetch_limit

These values influence infrastructure efficiency only.

They do **not** modify:

- HCP semantics;
- compatibility scores;
- correlation rules;
- Humanitarian Case construction.

---

# 11. Future Evolution

The storage architecture intentionally leaves room for future capabilities including:

- distributed nodes;
- federated searches;
- replicated databases;
- read replicas;
- storage sharding;
- cloud-native deployments;
- alternative indexing engines.

Those improvements should remain transparent to SearchService and to the HCP protocol.

---

# 12. Guiding Principles

The storage architecture follows three permanent principles.

> **Storage is infrastructure. HCP is behavior.**

Infrastructure may evolve without changing protocol semantics.

> **Application services depend on abstractions, not implementations.**

Persistence engines are interchangeable.

> **The Human Connection Protocol must never depend on a storage technology. Storage technologies must adapt to the Human Connection Protocol.**

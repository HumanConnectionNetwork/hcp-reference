# HCP Reference Implementation

Canonical reference implementation of the **Humanitarian Connection Protocol (HCP)**.

HCP is an open protocol for creating, validating, storing, searching and correlating structured humanitarian observations.

This repository demonstrates how the protocol can be implemented in a small, understandable and specification-aligned HCP Node.

> The specification defines the protocol.
> The reference implementation demonstrates the protocol.

---

## Purpose

The HCP Reference Implementation exists to provide developers with a practical example of how to implement the Humanitarian Connection Protocol.

It demonstrates:

* Canonical Humanitarian Record validation
* Humanitarian Record creation
* Local record storage
* Record retrieval by ID
* Query processing
* Local search
* Local correlation
* Humanitarian Case generation
* Basic HTTP API behavior

The implementation intentionally remains small and educational.

It is not intended to become a production humanitarian platform.

---

## Protocol Authority

The official protocol is defined in the `hcp-specification` repository.

This repository must remain aligned with that specification.

When the reference implementation and the specification disagree, the specification takes precedence.

The reference implementation must not create new protocol rules or redefine existing behavior.

---

## Core Principle

HCP stores humanitarian observations, not identities.

A Humanitarian Record describes an observation involving a human or animal during a humanitarian situation.

It does not create:

* a personal identity;
* a permanent personal profile;
* a medical history;
* a centralized registry;
* a definitive identity match.

Independent observations may be compared through correlation rules and presented as possible related cases.

Correlation expresses probability, not certainty.

---

## Current Scope

The reference implementation focuses on local protocol behavior.

### Included

* Humanitarian Record models
* Canonical field validation
* Human and animal subjects
* JSON persistence
* Search by record ID
* Structured queries
* Local search
* Local correlation
* Explainable correlation results
* Humanitarian Case generation
* Minimal FastAPI interface
* Health endpoint
* Canonical examples
* Unit, integration and conformance tests

### Not Included

The following capabilities belong to production implementations or future implementation phases:

* Authentication
* Authorization
* Federation
* Node discovery
* Distributed search
* Inter-node synchronization
* Digital signatures
* Operational monitoring
* Rate limiting
* Production databases
* User management
* Human identity verification

These capabilities may be implemented by production HCP Nodes without changing the protocol itself.

---

## Architecture

```text
Client or Application
        │
        ▼
HTTP API
        │
        ▼
Application Services
        │
        ▼
HCP Domain Models
        │
        ▼
Storage Interface
        │
        ▼
Local JSON Store
```

The implementation separates protocol data models, application logic, storage and HTTP transport.

```text
app/
├── api/
├── core/
├── models/
├── services/
└── storage/
```

### `app/models`

Contains protocol-aligned data models:

* Humanitarian Record
* Query
* Correlation result
* Humanitarian Case

### `app/services`

Contains application behavior:

* Record creation and retrieval
* Search processing
* Correlation
* Humanitarian Case generation

### `app/storage`

Contains the local persistence interface and JSON storage implementation.

### `app/api`

Exposes the minimal HTTP API.

### `app/core`

Contains configuration and shared application errors.

---

## Repository Structure

```text
hcp-reference/
├── app/
│   ├── api/
│   │   ├── correlation.py
│   │   ├── health.py
│   │   ├── records.py
│   │   └── search.py
│   │
│   ├── core/
│   │   ├── config.py
│   │   └── errors.py
│   │
│   ├── models/
│   │   ├── correlation.py
│   │   ├── humanitarian_case.py
│   │   ├── humanitarian_record.py
│   │   └── query.py
│   │
│   ├── services/
│   │   ├── correlation.py
│   │   ├── records.py
│   │   └── search.py
│   │
│   ├── storage/
│   │   ├── base.py
│   │   └── json_store.py
│   │
│   └── main.py
│
├── data/
├── docs/
├── examples/
├── tests/
├── requirements.txt
└── requirements-dev.txt
```

---

## HTTP API

The reference node will expose a minimal API for:

```text
GET  /
GET  /health
POST /hcp/records
GET  /hcp/records/{record_id}
POST /hcp/search
POST /hcp/correlation
```

The exact request and response formats follow the official HCP specification.

The API layer does not define protocol behavior. It only exposes the implementation through HTTP.

---

## Local Storage

Humanitarian Records are stored locally as canonical JSON.

The reference implementation uses a JSON file to keep persistence:

* easy to inspect;
* easy to understand;
* independent of database software;
* appropriate for educational and conformance purposes.

Production nodes may use relational, document or distributed databases while preserving the same protocol semantics.

---

## Search and Correlation

Search retrieves records that satisfy a structured HCP Query.

Correlation compares independent observations and estimates whether they may describe the same humanitarian situation.

Possible correlation signals include:

* reported name similarity;
* estimated age;
* reported location;
* temporal proximity;
* visible characteristics;
* event type;
* reporting source;
* animal species, size or breed.

Correlation results must remain explainable.

The implementation should identify which signals contributed to a result and must never present a probabilistic correlation as confirmed identity.

---

## Humanitarian Cases

A Humanitarian Case is a presentation of records that may be related.

It does not replace or modify the original Humanitarian Records.

Humanitarian Records remain independent and immutable observations.

A case is generated from search and correlation results and can change when new observations become available.

---

## Installation

Create a Python virtual environment:

```bash
python -m venv .venv
```

Activate it.

Linux or macOS:

```bash
source .venv/bin/activate
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Install the runtime dependencies:

```bash
pip install -r requirements.txt
```

Install development dependencies:

```bash
pip install -r requirements-dev.txt
```

---

## Running the Reference Node

```bash
uvicorn app.main:app --reload
```

The API will be available at:

```text
http://127.0.0.1:8000
```

Interactive API documentation:

```text
http://127.0.0.1:8000/docs
```

---

## Tests

Run the complete test suite:

```bash
pytest
```

The test suite is organized into:

```text
tests/unit/
tests/integration/
tests/conformance/
```

### Unit tests

Validate isolated models, services and correlation behavior.

### Integration tests

Validate HTTP endpoints, storage and complete application flows.

### Conformance tests

Validate canonical examples and protocol-aligned behavior against the official specification.

---

## Relationship with the HCN Ecosystem

```text
hcp-specification
    Defines the protocol

hcp-reference
    Demonstrates a minimal implementation

HCP SDKs
    Encapsulate protocol construction and communication

hcp-node-web
    Production-oriented HCP Node

hcp-client-telegram
    First operational HCP Client

redconexionhumana.org
    Public Web Client and humanitarian platform
```

The reference implementation may serve as technical guidance for future SDKs and production nodes, but production-specific behavior must remain outside this repository.

---

## Development Principles

Contributions should preserve the following principles:

* Specification first
* Simple implementation
* Shared semantics
* Data minimization
* Explainable correlation
* Transport independence
* Storage independence
* No central identity registry
* No hidden protocol behavior
* No production complexity without reference value

Every implemented feature should clearly demonstrate a requirement already defined by the HCP specification.

---

## Status

The project is currently consolidating the reference implementation around the stable HCP core specification.

The immediate objective is to complete:

* canonical models;
* local persistence;
* record operations;
* search;
* correlation;
* Humanitarian Case generation;
* API endpoints;
* examples;
* conformance tests.

Protocol expansion is not the objective of this repository.

Implementation feedback may reveal ambiguities that should be addressed in the official specification.

---

## Contributing

Contributions are welcome.

Before contributing, read:

* `CONTRIBUTING.md`
* `CODE_OF_CONDUCT.md`
* `docs/architecture.md`
* the official HCP specification

Implementations must remain compatible with the protocol and should avoid adding behavior that belongs exclusively to a specific client or production platform.

---

## License

Licensed under the Apache License 2.0.

See `LICENSE` for details.

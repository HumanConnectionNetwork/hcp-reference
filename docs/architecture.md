# HCP Reference Architecture

## Overview

This document describes the architecture of the **HCP Reference Implementation**.

The purpose of this repository is to provide a working implementation of the **Humanitarian Connection Protocol (HCP)** as defined in `hcp-specification`.

HCP is a protocol for exchanging **humanitarian observations**, not personal identities.

This implementation is designed to demonstrate how an HCP-compatible node can:

- create Humanitarian Records;
- validate records;
- store records locally;
- search humanitarian observations;
- correlate possible related observations;
- evaluate trust;
- synchronize with other nodes;
- participate in federations.

---

## Core Principle

HCP does not maintain a registry of people.

It does not create personal histories.

It does not identify individuals with certainty.

Instead, HCP stores and exchanges immutable humanitarian observations related to extraordinary events.

A Humanitarian Record may contain information about a person observed during a humanitarian event, but the record itself is not a personal identity.

---

## Architectural Goal

The reference implementation should be:

- simple to understand;
- faithful to the HCP specification;
- modular;
- testable;
- secure by design;
- privacy-oriented;
- suitable as a base for other implementations.

This repository is not intended to become a production platform by itself.

It is a reference implementation.

---

## High-Level Architecture

```text
Client / Application / Bot
        │
        ▼
HCP API Layer
        │
        ▼
Application Services
        │
        ▼
HCP Core Modules
        │
        ▼
Storage Layer
        │
        ▼
Local Database
```

External nodes communicate through synchronization, search and federation interfaces.

```text
HCP Node A
    │
    ├── Local Records
    ├── Search
    ├── Correlation
    ├── Trust
    └── Sync
          │
          ▼
HCP Node B / Federation Peer
```

---

## Main Components

## 1. API Layer

Location:

```text
app/api/
```

The API layer exposes the external interface of the node.

Expected modules:

```text
records.py
search.py
sync.py
nodes.py
```

Responsibilities:

- receive record creation requests;
- expose local search;
- expose synchronization endpoints;
- expose basic node metadata;
- validate incoming requests before passing them to services.

The API layer should not contain business logic.

---

## 2. Core Configuration

Location:

```text
app/core/
```

Expected modules:

```text
config.py
errors.py
```

Responsibilities:

- application settings;
- environment configuration;
- shared error definitions;
- global constants;
- startup configuration.

---

## 3. HCP Domain Modules

Location:

```text
app/hcp/
```

This is the conceptual heart of the implementation.

Expected structure:

```text
app/hcp/
├── records/
├── validation/
├── correlation/
├── trust/
├── sync/
├── search/
├── federation/
└── security/
```

---

## 3.1 Records Module

Location:

```text
app/hcp/records/
```

Responsibilities:

- create Humanitarian Records;
- assign UUIDs;
- apply timestamps;
- preserve immutability;
- prepare records for storage and synchronization.

A record must never be updated after publication.

New information creates a new record.

---

## 3.2 Validation Module

Location:

```text
app/hcp/validation/
```

Responsibilities:

- validate required fields;
- validate event classification;
- validate timestamps;
- validate UUID format;
- validate protocol version;
- reject malformed records.

Validation confirms structural correctness.

It does not confirm that the humanitarian observation is true.

---

## 3.3 Correlation Module

Location:

```text
app/hcp/correlation/
```

Responsibilities:

- compare independent Humanitarian Records;
- estimate whether records may describe the same humanitarian situation;
- calculate Correlation Candidate scores;
- preserve original records without merging them.

Correlation is probabilistic.

It must never create a confirmed identity.

---

## 3.4 Trust Module

Location:

```text
app/hcp/trust/
```

Responsibilities:

- calculate Trust Scores;
- evaluate reporting source reliability;
- distinguish authenticity from credibility;
- support trust policies defined by the local node or federation.

A signed record may still be inaccurate.

Trust evaluation is separate from cryptographic verification.

---

## 3.5 Search Module

Location:

```text
app/hcp/search/
```

Responsibilities:

- search local Humanitarian Records;
- support partial and approximate search parameters;
- return observations, not people;
- prepare results for correlation and ranking.

Search queries may include:

- reported name;
- estimated age;
- location;
- date range;
- event classification;
- keywords.

---

## 3.6 Synchronization Module

Location:

```text
app/hcp/sync/
```

Responsibilities:

- exchange Humanitarian Records with peer nodes;
- detect missing records;
- ignore duplicate UUIDs;
- validate incoming records;
- preserve signatures and metadata;
- support incremental synchronization.

Synchronization transfers observations.

It does not transfer personal profiles.

---

## 3.7 Federation Module

Location:

```text
app/hcp/federation/
```

Responsibilities:

- manage known peers;
- support private, public or hybrid federations;
- enforce federation policies;
- define which peers may synchronize or search;
- support future node discovery mechanisms.

HCP does not require a global network.

Each federation is autonomous.

---

## 3.8 Security Module

Location:

```text
app/hcp/security/
```

Responsibilities:

- digital signatures;
- signature verification;
- record integrity;
- node authentication;
- replay protection;
- audit-related helpers.

Security proves origin and integrity.

It does not prove humanitarian truth.

---

## 4. Storage Layer

Location:

```text
app/storage/
```

Expected modules:

```text
database.py
models.py
repositories.py
```

Responsibilities:

- persist Humanitarian Records;
- preserve immutability;
- retrieve records by UUID;
- support local search indexes;
- store peer metadata;
- store synchronization state.

The storage layer must not convert records into personal identity profiles.

---

## 5. Schemas

Location:

```text
app/schemas/
```

Expected modules:

```text
humanitarian_record.py
search_request.py
search_response.py
sync.py
```

Responsibilities:

- define request and response models;
- validate API input;
- serialize HCP-compatible structures;
- preserve compatibility with JSON schemas from `hcp-specification`.

Schemas should remain close to the official specification.

---

## 6. Data Flow: Creating a Record

```text
Client
  │
  ▼
POST /records
  │
  ▼
API Layer
  │
  ▼
Validation Module
  │
  ▼
Records Module
  │
  ▼
Storage Layer
  │
  ▼
Local Database
```

A successful record creation stores a new immutable Humanitarian Record.

---

## 7. Data Flow: Searching Records

```text
Client
  │
  ▼
POST /search
  │
  ▼
Search Module
  │
  ▼
Local Records
  │
  ▼
Correlation Module
  │
  ▼
Trust Module
  │
  ▼
Ranked Result Presentation
```

The result is a ranked list of humanitarian observations or candidates.

The result must not claim definitive personal identification.

---

## 8. Data Flow: Synchronization

```text
Peer Node
  │
  ▼
Sync Endpoint
  │
  ▼
Security Verification
  │
  ▼
Validation Module
  │
  ▼
Duplicate Detection
  │
  ▼
Storage Layer
```

Incoming records must be validated before storage.

Duplicate UUIDs are ignored.

Modified signed records are rejected.

---

## 9. Immutability Rule

Humanitarian Records are immutable.

This is prohibited:

```text
Record A: Missing
        ↓
Record A: Hospitalized
```

This is correct:

```text
Record A: Missing

Record B: Hospitalized
```

The humanitarian timeline is reconstructed later through search and correlation.

---

## 10. Privacy Boundary

The reference implementation must avoid becoming:

- a person registry;
- a medical record system;
- a government registry;
- a donor management platform;
- a humanitarian CRM;
- an identity provider.

Applications may build those systems separately, but HCP only exchanges humanitarian observations.

---

## 11. Relationship with Applications

Applications such as Telegram bots, emergency dashboards or RedConexionHumana may use this implementation to create and query HCP records.

However, application-specific workflows remain outside HCP.

Example:

RedConexionHumana may verify a beneficiary internally.

That verification belongs to RedConexionHumana.

It does not become a universal HCP identity status.

---

## 12. Implementation Roadmap

Recommended development order:

```text
1. Humanitarian Record schema
2. Record validation
3. Local storage
4. POST /records
5. GET /records/{uuid}
6. Local search
7. Correlation Candidate
8. Trust Score
9. Synchronization
10. Federation
11. Security hardening
12. Conformance tests
```

---

## 13. Testing Strategy

Tests should cover:

- valid records;
- invalid records;
- immutability;
- duplicate detection;
- search behavior;
- correlation behavior;
- trust evaluation;
- synchronization;
- privacy boundaries;
- interoperability with official examples.

Conformance tests should eventually prove compatibility with `hcp-specification`.

---

## 14. Summary

The HCP Reference Implementation provides a practical model for building HCP-compatible nodes.

Its architecture follows one central principle:

> HCP exchanges humanitarian observations, not identities.

The implementation should remain modular, interoperable, secure and privacy-oriented while staying faithful to the official HCP specification.

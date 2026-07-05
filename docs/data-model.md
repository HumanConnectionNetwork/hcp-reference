# HCP Reference Data Model

## Overview

This document describes the conceptual data model used by the HCP Reference Implementation.

The data model follows the Humanitarian Connection Protocol (HCP) specification and is intentionally centered on **humanitarian observations**, not personal identities.

The objective of the model is to support interoperability between independent HCP Nodes while preserving immutability, decentralization and privacy.

---

# Core Design Principle

The HCP data model stores **Humanitarian Records**.

It does **not** store people.

A Humanitarian Record may contain information reported about an individual involved in a humanitarian event, but the protocol never creates or manages a persistent identity.

Instead, it stores immutable observations.

---

# Conceptual Model

```text
Humanitarian Record
        │
        ├── Metadata
        ├── Event Classification
        ├── Observation
        ├── Source
        ├── Signature
        └── Synchronization Metadata
```

Additional processing components operate on these records without modifying them.

---

# Primary Entities

The reference implementation is organized around the following conceptual entities.

---

# Humanitarian Record

The Humanitarian Record is the fundamental unit of information exchanged by HCP.

Every record represents one humanitarian observation made at a specific moment.

Responsibilities:

- unique identifier
- timestamp
- humanitarian observation
- event classification
- source metadata
- synchronization metadata

Records are immutable.

---

# Event Classification

Defines the standardized humanitarian event vocabulary.

Examples include:

- Missing Person Observation
- Hospital Admission
- Rescue Completed
- Shelter Registration
- Food Distribution
- Building Collapse

The classification enables interoperability between independent implementations.

---

# Observation

Represents the humanitarian information reported at the time the record was created.

Typical information may include:

- reported name
- estimated age
- estimated location
- humanitarian status
- textual description

The observation reflects what was known at that moment.

It is never updated.

---

# Source

Describes where the observation originated.

Examples:

- Hospital
- Fire Department
- Civil Defense
- NGO
- Volunteer
- Family Member

The source contributes to trust evaluation but does not determine truth.

---

# Node Identity

Represents the HCP Node that created or synchronized the record.

Typical attributes include:

- Node UUID
- public identity
- federation membership
- protocol version

Node Identity belongs to the node.

It does not describe the observed individual.

---

# Correlation Candidate

Represents a temporary analytical result.

It estimates that multiple Humanitarian Records may describe the same humanitarian situation.

Correlation Candidates are generated dynamically.

They are not synchronized as Humanitarian Records.

---

# Trust Evaluation

Represents the confidence assigned to one or more observations.

Trust may consider:

- source reliability
- observation consistency
- independent confirmations
- historical behavior

Trust is calculated.

It is not stored as part of the original Humanitarian Record.

---

# Search Request

Represents a humanitarian search performed by a user or another node.

Typical fields include:

- reported name
- estimated age
- event classification
- location
- date range
- keywords

Search Requests are transient.

---

# Search Response

Represents the ordered result returned by a search operation.

Contains:

- Humanitarian Records
- Correlation Candidates
- Trust Scores
- ranking information

The response never identifies individuals with certainty.

---

# Synchronization Metadata

Synchronization metadata supports record exchange between nodes.

Examples include:

- synchronization timestamp
- originating node
- protocol version
- signature status

Synchronization metadata never alters the original observation.

---

# Federation Peer

Represents another HCP Node known to the local implementation.

Typical attributes:

- Node UUID
- endpoint
- federation
- supported protocol version
- synchronization capabilities

Peer information is operational metadata.

It is independent from Humanitarian Records.

---

# Relationships

The conceptual relationships are illustrated below.

```text
Node
 │
 ├──────────────┐
 │              │
 ▼              ▼
Humanitarian Record
        │
        ├── Event Classification
        ├── Observation
        ├── Source
        └── Synchronization Metadata
                │
                ▼
         Correlation Candidate
                │
                ▼
          Trust Evaluation
                │
                ▼
          Search Response
```

---

# Immutability

Once created:

```text
Humanitarian Record
```

must never be modified.

Instead:

```text
Observation A

↓

Observation B

↓

Observation C
```

The humanitarian timeline is reconstructed dynamically.

---

# Local Storage

Nodes may implement any storage technology.

Examples include:

- PostgreSQL
- SQLite
- MySQL
- MongoDB
- distributed databases
- embedded storage

The protocol does not mandate a storage engine.

---

# Local Extensions

Implementations may create additional internal entities.

Examples include:

- authentication users
- federation configuration
- audit logs
- synchronization queues
- cache tables
- search indexes

These internal entities are outside the HCP protocol.

---

# Outside the Scope of HCP

The following concepts are intentionally excluded from the HCP data model:

- persistent personal identities
- medical records
- hospital information systems
- donor databases
- beneficiary management
- case management systems
- financial transactions
- biometric repositories
- government registries

Applications may maintain such data independently.

They are not synchronized through HCP.

---

# Reference Implementation Philosophy

The HCP Reference Implementation separates:

- storage;
- search;
- correlation;
- trust;
- synchronization;
- federation;
- security.

Each module has a single responsibility.

This modularity allows future implementations to replace individual components without changing protocol behavior.

---

# Summary

The HCP data model is centered on immutable Humanitarian Records.

Records describe humanitarian observations rather than individuals.

Additional components such as Correlation Candidate, Trust Evaluation and Search Responses are derived dynamically from these observations.

This model preserves interoperability, decentralization and privacy while remaining faithful to the principles defined by the Humanitarian Connection Protocol.

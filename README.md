# HCP Reference Implementation

> Official reference implementation of the Humanitarian Connection Protocol (HCP)

![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)
![Status](https://img.shields.io/badge/status-Prototype-orange.svg)
![Specification](https://img.shields.io/badge/specification-HCP-green.svg)

---

## Overview

The **HCP Reference Implementation** is the official open-source implementation of the **Humanitarian Connection Protocol (HCP)**.

Its purpose is to demonstrate how HCP can be implemented consistently while serving as a practical reference for developers, organizations and humanitarian initiatives.

This repository is **not** the HCP specification itself.

The specification lives in the companion repository:

> **hcp-specification**

This project implements that specification.

---

# What is HCP?

The **Humanitarian Connection Protocol (HCP)** is an open protocol for exchanging humanitarian observations between independent systems.

HCP enables hospitals, emergency responders, NGOs, governments, volunteer organizations and humanitarian platforms to exchange structured humanitarian information without relying on a centralized database.

HCP synchronizes **Humanitarian Records**, not people.

---

# Purpose of this Repository

This repository provides a complete reference implementation of HCP.

It demonstrates:

- Humanitarian Record creation
- Record validation
- Record synchronization
- Distributed search
- Correlation Candidate processing
- Trust evaluation
- Federation support
- Security mechanisms

It serves as the canonical implementation used to validate the protocol specification.

---

# Design Principles

The reference implementation follows the principles defined by HCP:

- Open
- Decentralized
- Interoperable
- Offline-first
- Immutable observations
- Federation-based
- Security by design
- Privacy by design

---

# Repository Structure

```text
app/

    api/
    core/
    hcp/
    storage/
    schemas/

docs/

examples/

tests/

scripts/
```

---

# Core Components

## Humanitarian Records

Creates and validates immutable Humanitarian Records.

---

## Search Engine

Implements distributed humanitarian search.

---

## Correlation Candidate

Estimates which independent observations are likely to describe the same humanitarian situation.

---

## Trust Model

Calculates observation credibility independently from cryptographic authenticity.

---

## Synchronization

Synchronizes Humanitarian Records across HCP Nodes.

---

## Federation

Supports communication between private, public and hybrid federations.

---

## Security

Implements:

- digital signatures
- integrity verification
- authentication
- replay protection
- audit logging

---

# What HCP Is

HCP is:

- a humanitarian interoperability protocol
- an open standard
- decentralized
- event-oriented
- observation-based

---

# What HCP Is Not

HCP is **not**:

- a centralized platform
- a hospital information system
- a medical record system
- a person registry
- a government database
- a humanitarian CRM
- an identity provider
- a donor management platform

Organizations may build those systems on top of HCP.

HCP only defines how humanitarian observations are exchanged.

---

# Relationship with Other Repositories

```text
Human Connection Network

│

├── hcp-specification
│       Defines the protocol
│
├── hcp-reference
│       Implements the protocol
│
├── hcp-node-telegram
│       Example HCP Node
│
└── red-conexion-humana
        Humanitarian platform using HCP
```

---

# Current Status

Current implementation targets:

- HCP Core Specification
- Humanitarian Records
- Distributed Search
- Synchronization
- Federation
- Trust Model
- Security Model

Additional features will be implemented as the specification evolves.

---

# Contributing

Contributions are welcome.

Before contributing, please read the HCP specification.

The reference implementation should remain fully compatible with the official protocol.

---

# License

Apache License 2.0

---

# Vision

Humanitarian emergencies generate fragmented information.

Different organizations observe different parts of the same reality.

HCP does not attempt to centralize those observations.

Instead, it provides a common language that allows independent organizations to exchange humanitarian information while preserving autonomy, privacy and interoperability.

**Because humanitarian collaboration should not depend on centralized infrastructure.**

# HCP Reference Architecture

## Overview

The HCP Reference project is the official reference implementation of the Humanitarian Connection Protocol (HCP).

Its purpose is to implement the HCP specification by providing a modular, interoperable and platform-independent foundation for humanitarian information management.

The reference implementation standardizes humanitarian records, identities and workflows, enabling different organizations and applications to exchange information using a common language.

HCP does not distribute humanitarian aid.

HCP standardizes humanitarian information so that aid can be coordinated more efficiently, transparently and securely.

---

# Architectural Principles

The HCP Reference implementation follows six fundamental principles.

## 1. Protocol First

The protocol is the primary concern.

Applications adapt to HCP.

HCP never adapts to a specific application.

---

## 2. Platform Independent

The implementation must remain independent from any client platform.

Telegram, Web, Mobile, CLI, WhatsApp or future applications are simply HCP-compatible clients.

---

## 3. Standardized Humanitarian Records

Every humanitarian record must comply with the HCP Specification.

Applications should never invent proprietary record formats that break interoperability.

---

## 4. Interoperability

The primary objective of HCP is interoperability.

Organizations should be able to exchange humanitarian information without custom integrations.

---

## 5. Trust Through Standardization

Reliable humanitarian coordination begins with reliable information.

By standardizing identities, records and workflows, HCP enables trustworthy collaboration between independent organizations.

---

## 6. Extensibility

New capabilities must be added without breaking compatibility.

The protocol should evolve through versioning rather than incompatible changes.

---

# High-Level Architecture

                    +------------------------------+
                    |      HCP-Compatible Clients  |
                    |------------------------------|
                    | Telegram Bot                 |
                    | Web Applications             |
                    | Mobile Apps                  |
                    | NGO Platforms                |
                    | Government Systems           |
                    +--------------+---------------+
                                   |
                                   |
                    +--------------v---------------+
                    |        HCP Reference         |
                    |------------------------------|
                    | API Layer                    |
                    | Protocol Engine              |
                    | Identity Registry            |
                    | Humanitarian Records         |
                    | Validation Engine            |
                    | Event Processing             |
                    +--------------+---------------+
                                   |
                                   |
                    +--------------v---------------+
                    |      Persistence Layer       |
                    |------------------------------|
                    | PostgreSQL                   |
                    | SQLite                       |
                    | Future Storage Providers     |
                    +------------------------------+

---

# Architectural Layers

## Client Layer

External applications interact with HCP through public interfaces.

Clients should never implement protocol logic internally.

Examples:

- Telegram Node
- Web Platform
- Mobile Application
- NGO Management System
- Government Information System

---

## API Layer

Responsible for receiving and exposing HCP operations.

This layer handles communication only.

Business rules belong elsewhere.

---

## Protocol Layer

The heart of the project.

Responsible for implementing:

- Humanitarian identities
- Humanitarian records
- Validation
- Protocol rules
- State transitions
- Workflow compliance

This layer must remain independent from user interfaces and storage technologies.

---

## Services Layer

Coordinates protocol operations.

Contains business logic shared across all clients.

---

## Persistence Layer

Stores humanitarian information.

The protocol must remain independent from any specific database technology.

---

# Repository Structure

```
app/
    api/
    core/
    protocol/
    services/
    storage/
    security/

docs/

schemas/

examples/

tests/
```

---

# Relationship with the HCP Specification

The specification defines the protocol.

This repository implements it.

Whenever differences exist, the HCP Specification is the authoritative source.

---

# Relationship with Client Applications

Applications consume HCP.

They do not define HCP.

Examples include:

- hcp-node-telegram
- Red Conexión Humana
- Mobile applications
- NGO software
- Government platforms

Each client may present different user experiences while remaining fully compatible with the protocol.

---

# Long-Term Vision

The HCP Reference implementation aims to become the canonical implementation of the Humanitarian Connection Protocol.

Its purpose is to help humanitarian organizations adopt a common standard for recording, validating and exchanging humanitarian information.

The implementation should remain:

- Open
- Modular
- Secure
- Interoperable
- Vendor Neutral

---

# Architecture Philosophy

The Humanitarian Connection Protocol does not distribute humanitarian aid.

The Humanitarian Connection Protocol standardizes humanitarian information.

By creating interoperable humanitarian records, HCP enables trustworthy collaboration between organizations and allows humanitarian applications to connect affected people with those willing to help.

**Standardize Data. Enable Trust. Decentralize Aid.**

# HCP Reference Architecture

## Overview

The HCP Reference project is the official reference implementation of the Humanitarian Connection Protocol (HCP).

Its purpose is to implement the protocol defined in the HCP Specification while remaining independent of any specific client application.

The reference implementation provides the core services required to create, validate, manage and exchange standardized humanitarian records.

Clients such as Telegram bots, web applications, mobile apps or third-party systems communicate with HCP through well-defined interfaces.

---

# Architectural Principles

The implementation follows five fundamental principles.

## 1. Protocol First

The protocol is the primary concern.

Applications adapt to HCP.

HCP does not adapt to applications.

---

## 2. Client Agnostic

The implementation must never depend on Telegram, WhatsApp, Web, Mobile or any specific platform.

All clients interact through the same interfaces.

---

## 3. Standardized Humanitarian Records

Every humanitarian record must follow the HCP specification.

Applications cannot invent proprietary record formats.

---

## 4. Interoperability

The reference implementation exists to maximize interoperability between humanitarian organizations.

Compatibility with the specification is more important than compatibility with any individual implementation.

---

## 5. Extensibility

New modules may be added without modifying the protocol itself.

Future clients and services should integrate without breaking compatibility.

---

# High-Level Architecture

                    +-------------------------+
                    |       Clients           |
                    |-------------------------|
                    | Telegram Bot            |
                    | Web Platform            |
                    | Mobile App              |
                    | NGO Systems             |
                    +------------+------------+
                                 |
                                 |
                    +------------v------------+
                    |      HCP Reference      |
                    |-------------------------|
                    | API                     |
                    | Protocol Engine         |
                    | Validation              |
                    | Identity Registry       |
                    | Record Registry         |
                    | Event Processing        |
                    +------------+------------+
                                 |
                                 |
                    +------------v------------+
                    |    Persistence Layer    |
                    |-------------------------|
                    | PostgreSQL              |
                    | SQLite                  |
                    | Future Datastores       |
                    +-------------------------+

---

# Core Components

## API Layer

Receives requests from compatible clients.

Responsible only for transport.

Contains no business rules.

---

## Protocol Layer

Implements the Humanitarian Connection Protocol.

Responsible for:

- record creation
- validation
- identity management
- workflow
- protocol compliance

This is the heart of the project.

---

## Services Layer

Coordinates protocol operations.

Contains business logic.

---

## Persistence Layer

Stores humanitarian records.

The protocol must remain independent from the storage technology.

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

This repository implements the protocol.

The protocol definition itself belongs to the HCP Specification repository.

Whenever conflicts exist, the specification is the authoritative source.

---

# Relationship with Client Applications

This project is not a client.

Projects such as:

- hcp-node-telegram
- future WhatsApp nodes
- mobile applications
- humanitarian platforms

consume this implementation through public interfaces.

---

# Long-Term Vision

The HCP Reference implementation aims to become the canonical implementation of the Humanitarian Connection Protocol, serving governments, NGOs, humanitarian organizations and open-source projects around the world.

The implementation should remain modular, interoperable and independent from any specific humanitarian initiative.

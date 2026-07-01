# HCP Data Model

## Purpose

The Humanitarian Connection Protocol (HCP) defines a common data model for recording, validating and exchanging humanitarian information.

Its goal is not to prescribe how applications store information internally, but to ensure that humanitarian information can be exchanged consistently between independent systems.

Every HCP-compatible implementation should represent these core entities, regardless of programming language, database technology or user interface.

---

# Design Principles

The HCP data model is based on the following principles:

- Human-centered
- Organization-independent
- Interoperable
- Extensible
- Versionable
- Privacy-aware

---

# Core Entities

HCP defines the following conceptual entities.

## Humanitarian Identity

Represents the unique humanitarian identity of an individual.

A Humanitarian Identity is created when a person is first registered by an HCP-compatible organization.

It is independent of:

- National ID
- Passport
- Government systems
- Specific organizations

Its purpose is to uniquely identify a person within humanitarian operations while minimizing duplication.

---

## Humanitarian Record

Represents a standardized record describing the humanitarian situation of a person or household.

A record may include:

- personal information
- household composition
- location
- vulnerability assessment
- current status
- humanitarian needs
- previous assistance
- verification history

A Humanitarian Record evolves over time through events.

---

## Organization

Represents an organization participating in the humanitarian ecosystem.

Examples include:

- NGOs
- Government agencies
- International organizations
- Community groups
- Volunteer networks

Organizations create and update humanitarian records according to the HCP Specification.

---

## Humanitarian Worker

Represents an authenticated individual acting on behalf of an organization.

Workers may:

- register people
- update records
- perform assessments
- verify information

Every action performed by a worker should be traceable.

---

## Household

Represents a family or group of individuals sharing humanitarian circumstances.

Multiple Humanitarian Identities may belong to the same household.

---

## Assessment

Represents the evaluation of humanitarian conditions.

Assessments may describe:

- shelter
- food security
- health
- water
- sanitation
- protection
- education
- other humanitarian dimensions

Assessments generate structured information rather than free-text descriptions whenever possible.

---

## Humanitarian Need

Represents an identified need resulting from an assessment.

Examples:

- Food
- Drinking water
- Shelter
- Medicine
- Transportation
- Clothing
- Communication
- Financial support

Needs are standardized to facilitate interoperability.

---

## Humanitarian Event

Represents any significant change affecting a Humanitarian Record.

Examples include:

- Person Registered
- Household Created
- Assessment Completed
- Need Identified
- Record Updated
- Verification Completed
- Assistance Delivered

The protocol is event-oriented.

Records evolve through events instead of being overwritten.

---

## Verification

Represents the verification status of humanitarian information.

Verification increases trust between independent organizations.

Possible verification methods may include:

- Field verification
- Organization verification
- Community verification
- Multi-source verification

The protocol does not prescribe a single verification mechanism.

---

# Relationships

The conceptual relationships are illustrated below.

```
Organization
      │
      │
      ▼
Humanitarian Worker
      │
      │
      ▼
Humanitarian Identity
      │
      ▼
Humanitarian Record
      │
      ├──────────────┐
      ▼              ▼
 Household      Assessment
                      │
                      ▼
              Humanitarian Need
                      │
                      ▼
              Humanitarian Event
                      │
                      ▼
                Verification
```

---

# Record Lifecycle

A Humanitarian Record typically follows this lifecycle.

```
Registration

↓

Assessment

↓

Needs Identified

↓

Verification

↓

Updates

↓

Assistance History

↓

Closure (optional)
```

Records should remain historically traceable.

Information is updated through protocol events rather than destructive modifications.

---

# Versioning

Every Humanitarian Record should include version information.

Changes must preserve historical integrity.

Compatible implementations should support future protocol versions without losing existing information.

---

# Interoperability

Applications may extend their internal data models.

However, every HCP-compatible implementation must preserve compatibility with the core entities defined by the protocol.

Extensions must never compromise interoperability.

---

# Relationship with Humanitarian Applications

The HCP data model standardizes humanitarian information.

Applications such as Red Conexión Humana consume this standardized information to provide services including:

- humanitarian coordination
- direct aid connections
- reporting
- logistics
- analytics

The protocol itself remains independent from any specific humanitarian application.

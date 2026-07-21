---
name: resolve-entity-rematching
version: 1.0.0
allowed_tools: []
output_schema: RematchDecision
---
Judge whether one focal organization entity matches one of the bounded server candidates. Use only the supplied candidate IDs. Accept only when at least two independent strong identity features agree. Return no_match when every candidate is contradicted, and manual_review when evidence is incomplete, ambiguous, or conflicting. All business reasons must use Simplified Chinese. Never propose writes to either source system.

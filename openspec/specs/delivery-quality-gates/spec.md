# Delivery quality gates

## Purpose

Define reproducible local and continuous-integration validation for backend, frontend,
and clean PostgreSQL migration readiness.

## Requirements

### Requirement: Verify clean PostgreSQL migrations
The system SHALL provide an automated migration smoke test that applies the complete Alembic history to an empty PostgreSQL database with pgvector enabled and verifies the resulting Alembic head and required schema objects.

#### Scenario: Clean PostgreSQL migration succeeds
- **WHEN** the migration validation receives a dedicated reachable PostgreSQL database URL
- **THEN** it recreates the disposable database state, enables required extensions, applies `alembic upgrade head`, and verifies the live database reaches the current Alembic head with required governance and reporting tables

#### Scenario: Local PostgreSQL is not configured
- **WHEN** the migration validation database URL is absent
- **THEN** the ordinary local backend test suite skips only the PostgreSQL smoke test with an explicit prerequisite message

### Requirement: Enforce backend and frontend quality gates
The continuous integration workflow SHALL run backend tests, Ruff, mypy, frontend unit tests, ESLint, TypeScript checking, and production build checks on every pull request and branch push.

#### Scenario: Frontend regression
- **WHEN** a frontend test, lint, type, or production build check fails
- **THEN** continuous integration fails independently of backend checks and identifies the failed frontend command

#### Scenario: Backend regression
- **WHEN** backend tests, Ruff, or mypy fail
- **THEN** continuous integration fails before the change can satisfy the delivery quality gate

### Requirement: Document reproducible verification
The repository documentation SHALL describe the exact local commands and prerequisites for backend checks, frontend checks, and clean PostgreSQL migration validation, consistent with the continuous integration workflow.

#### Scenario: Developer verifies a change locally
- **WHEN** a developer follows the documented verification guide on a machine with Python, Node, and Docker installed
- **THEN** they can run the same categories of quality checks as continuous integration without requiring real model credentials or production data

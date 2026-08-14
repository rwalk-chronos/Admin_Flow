# AdminFlow

AdminFlow is a local-first administrative workflow engine. It is designed to turn incoming unstructured information into structured, reviewable work items while keeping workflow state and business rules deterministic.

## Initial architecture

- **Backend:** Python + FastAPI
- **Database:** PostgreSQL
- **AI layer:** replaceable local AI provider (to be added after the core engine)
- **Workflow control:** deterministic application code
- **Deployment:** Docker Compose on Linux

## Development principle

AI interprets messy information. Deterministic code validates data, controls workflow state, creates actions, and records history. Humans review when the workflow requires approval.

## First milestone

The first vertical slice will provide:

1. A FastAPI service.
2. PostgreSQL connectivity.
3. Docker Compose development environment.
4. A `/health` endpoint.
5. Automated tests.

Healthcare-specific workflows are intentionally not part of the foundation yet.

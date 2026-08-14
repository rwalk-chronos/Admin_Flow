# AdminFlow agent instructions

## Purpose
AdminFlow is a local-first administrative workflow engine. The core product is the deterministic workflow engine; AI is a replaceable interpretation helper.

## Architecture rules
- Backend: Python + FastAPI.
- Database: PostgreSQL is the source of truth.
- AI may classify, extract, summarize, and draft.
- AI must return structured data at system boundaries.
- AI must not directly control workflow state transitions.
- Workflow state, validation, timers, permissions, and actions are deterministic application logic.
- Preserve original source material and link it to derived work items.
- Keep external integrations behind adapters/connectors.
- Prefer a modular monolith over microservices until scale proves otherwise.
- Keep dependencies minimal and boring.

## Development rules
- Add or update tests for every behavior change.
- Run the relevant tests before declaring work complete.
- Do not silently change database schemas.
- Keep functions and modules small enough to review.
- Do not introduce Kubernetes, Kafka, LangChain, autonomous-agent orchestration, or a vector database unless explicitly requested.
- Do not add healthcare-specific workflow assumptions to the core engine unless explicitly requested.

## Current milestone
Build the smallest runnable foundation:
1. FastAPI backend.
2. PostgreSQL connectivity.
3. Docker Compose development environment.
4. Health endpoints.
5. Automated tests.

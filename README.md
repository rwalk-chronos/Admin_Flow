# AdminFlow

AdminFlow is a local-first administrative workflow engine. It is designed to turn incoming unstructured information into structured, reviewable work items while keeping workflow state and business rules deterministic.

> **Development bootstrap:** This repository currently contains a runnable backend, PostgreSQL persistence, health checks, migration infrastructure, domain-neutral intake/artifact foundations, document reading, AI interpretation helpers, and a deterministic WorkItem workflow foundation. It is not a production-ready AdminFlow application and does not yet contain actions, timers, permissions, or human-review behavior.

## Architecture

- **Backend:** Python 3.12 and FastAPI
- **Database:** PostgreSQL 16 through SQLAlchemy and psycopg
- **Migrations:** Alembic
- **AI layer:** replaceable provider behind a narrow structured-data interface
- **Workflow control:** deterministic application code
- **Local deployment:** Docker Compose on Linux

AI interprets messy information. Deterministic code validates data, controls workflow state, creates actions, and records history. Humans will review work when the workflow requires approval.

## Prerequisites

For the Docker Compose workflow:

- Docker Engine with the Compose plugin
- `curl` for the endpoint examples

For running the backend or tests directly on the host:

- Python 3.12 or newer
- A reachable PostgreSQL 16 instance for migrations and integration tests

## Local Python setup

Create a virtual environment and install the backend with development dependencies:

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
```

The default `.env` connects to PostgreSQL at `localhost:5432` with the local development credentials. Start PostgreSQL with Compose if you do not already have a compatible instance:

```bash
docker compose up -d postgres
```

Run the API from `backend/`:

```bash
uvicorn app.main:app --reload
```

## Docker Compose

Build and start PostgreSQL and the API:

```bash
docker compose up --build
```

Run in the background with `docker compose up --build -d`. Stop the stack without deleting database data:

```bash
docker compose down
```

The development defaults can be overridden when invoking Compose:

```bash
POSTGRES_USER=myuser \
POSTGRES_PASSWORD=mypassword \
POSTGRES_DB=mydatabase \
POSTGRES_HOST_PORT=55432 \
  docker compose up --build
```

The variables configure the PostgreSQL container and the backend connection. `POSTGRES_HOST_PORT` changes only the port exposed on the host; containers continue to communicate over port 5432.

The named `adminflow_postgres` volume preserves data between normal shutdowns. Running `docker compose down --volumes` deletes that local database volume and its data.

## Health endpoints

With the stack running on its default port:

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{"status":"ok","service":"AdminFlow"}
```

Check live PostgreSQL connectivity through the backend:

```bash
curl http://localhost:8000/health/database
```

A successful check returns HTTP 200:

```json
{"status":"ok","database":"postgresql"}
```

A failed database check returns HTTP 503 and logs the underlying connection error in the backend service.

## Tests

Install the development dependencies, then run the default suite from `backend/`:

```bash
pytest
```

The PostgreSQL integration test is skipped by default. To run it against the Compose database:

```bash
ADMINFLOW_RUN_DATABASE_INTEGRATION_TESTS=1 pytest -m integration
```

`DATABASE_URL` may be set to test another database. GitHub Actions starts a dedicated PostgreSQL service and enables the integration test automatically. AI classification and structured-extraction tests inject stub providers and do not make external model calls.

## Database migrations

Alembic reads the same `DATABASE_URL` configuration as the application. The initial migration is an empty baseline and creates no AdminFlow domain tables.

From `backend/`, apply all migrations to the configured database:

```bash
alembic upgrade head
```

Alternatively, run the migration command in the Compose backend image from the repository root:

```bash
docker compose run --rm backend alembic upgrade head
```

Inspect the current and available revisions:

```bash
alembic current
alembic history
```

After future SQLAlchemy models are introduced, create a migration explicitly and review it before applying it:

```bash
alembic revision -m "describe the schema change"
```

## Local artifact storage

Original IntakeArtifact bytes are stored on the local filesystem, not in PostgreSQL. PostgreSQL stores only immutable artifact metadata and a generated internal storage key. Files are written in chunks while SHA-256 and byte size are calculated.

For host development, `ARTIFACT_STORAGE_PATH` defaults to `data/artifacts` relative to the backend working directory and can be changed in `backend/.env`:

```dotenv
ARTIFACT_STORAGE_PATH=/path/to/local/artifacts
```

In Docker Compose, the backend uses `/data/artifacts`. The named `adminflow_artifacts` volume persists uploaded files across normal container shutdowns. As with the PostgreSQL volume, `docker compose down --volumes` permanently removes this development artifact volume and its stored files.

## Native PDF document reading

AdminFlow can deterministically extract native text from PDF IntakeArtifacts with pypdf. Each extraction preserves 1-based page boundaries, character counts, and whether each page lacks meaningful native text and will require OCR later. Password-protected and corrupt PDFs produce persisted diagnostic extraction statuses without modifying the original artifact.

When native extraction marks pages as requiring OCR, the selective OCR endpoint rasterizes only those pages at 300 DPI and runs Tesseract. Native page text is preserved exactly in a new immutable derived extraction:

```text
native PDF text extraction
           ↓
selective OCR only for pages requiring it
```

OCR defaults can be overridden with `OCR_LANGUAGE`, `OCR_DPI`, and `OCR_TIMEOUT_SECONDS`. The current foundation supports English PDF OCR.

## AI document classification

Readable `DocumentExtraction` text can be classified into an application-supplied candidate taxonomy. The taxonomy is sent with each request so the core engine does not hard-code industry-specific document types.

The first provider adapter uses the OpenAI Responses API with structured output. Configure it in `backend/.env` or the shell:

```dotenv
OPENAI_API_KEY=your-key-here
AI_CLASSIFICATION_MODEL=gpt-5-mini
```

If `OPENAI_API_KEY` is not configured, classification requests return HTTP 503. Extraction, OCR, health, and storage endpoints continue to operate normally.

Classify a readable extraction:

```bash
curl -X POST \
  http://localhost:8000/document-extractions/EXTRACTION_ID/classifications \
  -H 'Content-Type: application/json' \
  -d '{
    "candidate_labels": [
      {
        "name": "procedure",
        "description": "Step-by-step instructions for performing a task"
      },
      {
        "name": "invoice",
        "description": "A request for payment for goods or services"
      }
    ]
  }'
```

The result is persisted as an immutable `DocumentClassification` with:

- source `document_extraction_id`
- the exact candidate-label snapshot
- provider, model, and prompt version
- selected label
- confidence from 0 to 1
- concise classification rationale

Classification does not transition workflow state or trigger actions. Those responsibilities remain in deterministic application logic and are intentionally outside this feature slice.

## AI structured data extraction

Readable `DocumentExtraction` text can be converted into validated structured data using field definitions supplied by the application. Supported V1 field types are `string`, `integer`, `number`, `boolean`, `date`, and `array_string`. A `DocumentClassification` may be supplied as optional context, but structured extraction does not require classification and never selects one automatically.

The provider is replaceable behind the `DocumentStructuredExtractor` interface. The OpenAI adapter reuses `OPENAI_API_KEY` and has a separate model setting:

```dotenv
AI_STRUCTURED_EXTRACTION_MODEL=gpt-5-mini
```

Create a structured extraction:

```bash
curl -X POST \
  http://localhost:8000/document-extractions/EXTRACTION_ID/structured-extractions \
  -H 'Content-Type: application/json' \
  -d '{
    "document_classification_id": null,
    "fields": [
      {
        "name": "title",
        "description": "Title of the document",
        "type": "string",
        "required": true
      },
      {
        "name": "effective_date",
        "description": "Date the document became effective",
        "type": "date",
        "required": false
      }
    ]
  }'
```

The immutable `DocumentStructuredExtraction` stores source lineage, the exact requested field-definition snapshot, validated extracted data, and provider/model/prompt metadata. Application code validates the exact field set, required/null behavior, scalar types, real ISO calendar dates, and string-array elements after every provider response. Structured extraction does not create WorkItems, transition workflow state, or trigger actions.

## WorkItems and deterministic workflows

A `WorkflowDefinition` is an immutable, application-defined graph of states and allowed transitions. Definitions are validated before persistence: state identifiers are constrained, terminal and reachability rules are checked, and cycles are accepted only when every nonterminal state still has a path to a terminal state.

A `WorkItem` holds current mutable workflow state and always retains required `IntakeEvent` lineage. It may also reference a `DocumentStructuredExtraction`; when it does, the application verifies the full source lineage and copies the structured result exactly into the WorkItem data snapshot. The source extraction remains immutable.

Every WorkItem begins at its workflow's `initial_state` with version 1 and an immutable creation transition. Later state changes are available only through the transition endpoint:

```bash
curl -X POST \
  http://localhost:8000/work-items/WORK_ITEM_ID/transitions \
  -H 'Content-Type: application/json' \
  -d '{
    "expected_state": "ready",
    "expected_version": 2,
    "to_state": "in_progress",
    "reason": "Work has started"
  }'
```

The deterministic transition engine verifies the exact allowed edge and uses `expected_state` plus `expected_version` to reject stale clients. WorkItem state/version and immutable transition history are committed atomically. Transition history is returned chronologically by version.

Workflow state names are application-defined and domain-neutral; generic examples include `new`, `ready`, `in_progress`, `waiting`, `completed`, and `cancelled`. AI never chooses or changes workflow state. This foundation does not yet implement actions, timers, permissions, authentication, or human approval behavior.

## Human review queue

Workflow states may set `review_required: true`. Transitions leaving such a state map the deterministic human decisions `approve` and optionally `reject` to fixed target states through `review_decision`; callers cannot choose an arbitrary post-review state.

When a WorkItem enters a review-required state, AdminFlow creates a pending immutable-version review record in the same transaction as the WorkItem transition. Normal transition requests cannot leave that state. Pending work is listed oldest first:

```bash
curl 'http://localhost:8000/work-item-reviews?status=pending'
curl http://localhost:8000/work-items/WORK_ITEM_ID/reviews
```

Resolve a review with an application-supplied reviewer audit identifier and optimistic state/version guard:

```bash
curl -X POST \
  http://localhost:8000/work-item-reviews/REVIEW_ID/resolve \
  -H 'Content-Type: application/json' \
  -d '{
    "decision": "approve",
    "expected_work_item_state": "human_review",
    "expected_work_item_version": 2,
    "reviewer": "reviewer-1",
    "notes": "Reviewed and approved"
  }'
```

Approval may include corrected `reviewed_data`. For WorkItems backed by a `DocumentStructuredExtraction`, corrections are deterministically validated against its exact persisted field schema; the immutable source extraction is never changed. Rejection never changes WorkItem data. Review resolution locks the WorkItem, uses the existing deterministic transition engine, and atomically persists the decision, WorkItem state/version, and immutable transition history.

The reviewer value is an application-supplied audit identifier in this V1 foundation. Authentication, RBAC, notifications, timers, actions, and a human-review frontend are not implemented. AI never approves, rejects, or changes workflow state.

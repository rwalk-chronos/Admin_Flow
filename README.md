# AdminFlow

AdminFlow is a local-first administrative workflow engine. It is designed to turn incoming unstructured information into structured, reviewable work items while keeping workflow state and business rules deterministic.

> **Development bootstrap:** This repository currently contains a runnable backend, PostgreSQL persistence, health checks, migration infrastructure, domain-neutral document processing, a deterministic WorkItem workflow, human review, deterministic Action Plans, native internal tasks, and a local dashboard. It is not a production-ready AdminFlow application and does not yet contain timers, permissions, authentication, or production connectors.

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

Open `http://localhost:8000/app/` to use the local dashboard. Visiting the API root at `http://localhost:8000/` redirects there.

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

Workflow state names are application-defined and domain-neutral; generic examples include `new`, `ready`, `in_progress`, `waiting`, `completed`, and `cancelled`. AI never chooses or changes workflow state. This foundation does not yet implement actions, timers, permissions, or authentication.

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

The reviewer value is an application-supplied audit identifier in this V1 foundation. Authentication, RBAC, notifications, and timers are not implemented. AI never approves, rejects, chooses an action, or changes workflow state.

## Action Plans and internal tasks

The application-owned `generic_office` processing profile creates an immutable, connectorless `create_internal_task` Action Plan alongside its review. The review screen explains exactly what approval will do, discloses that no external message will be sent, keeps the original document visible, and requires authorization of the exact current plan ID and facts snapshot.

`GET /work-item-reviews/{id}/decision-packet` provides the human-facing review projection without adding another persisted record or AI request. It follows immutable classification and extraction lineage server-side and returns a plain-language document type and confidence band, deterministic summary, readable key information, attention items, original artifacts, current Action Plan presentation, and the correction contract. The browser defaults to this read-first Decision Packet; correction inputs appear only after **Correct Information**, and **Review Changes** revises the immutable Action Plan before authorization is offered again.

If reviewed facts change, `POST /work-item-reviews/{id}/action-plan` validates them and creates a new immutable revision; the previous plan is retained as superseded. Approval creates one `ActionExecution` and one `InternalTask` transactionally using the Action Plan as the idempotency identity. Approval, execution success, and workflow transitions remain separate audit facts. **Handle Manually** preserves the source and review context without executing the plan.

Action history is available through the WorkItem detail screen and these APIs:

```text
GET /work-items/{id}/action-plans
GET /action-plans/{id}
GET /action-plans/{id}/executions
GET /internal-tasks
GET /internal-tasks/{id}
GET /work-item-reviews/{id}/decision-packet
GET /work-items/{id}/decision-packet
```

No email, fax, calendar, EHR, CRM, or other external connector is invoked by this action slice.

## Local dashboard

The basic office dashboard is served by FastAPI at `http://localhost:8000/app/`. It uses static HTML, local CSS, and vanilla JavaScript with no Node build, CDN, external font, analytics, telemetry, or internet dependency.

The dashboard provides:

- an overview of pending reviews, open and terminal WorkItems, and recent intake
- an oldest-first human review queue with approved and rejected history filters
- split-screen source-document preview and read-first Decision Packet with optional correction mode
- read-only WorkItem state, lineage, transition, and review history
- recent IntakeEvents and immutable artifact viewing

Approval and rejection use the existing deterministic review endpoint. The browser supplies the expected WorkItem state/version but never selects the target state. PDF files are fetched from local artifact storage as browser blobs and are not placed in URLs or browser storage. Only the reviewer convenience value is retained in local browser storage.

There is no authentication yet, and the reviewer field is only an application-supplied audit identifier. The dashboard now includes a local manual-intake form; production connectors remain outside this proof-of-concept slice.

## Manual intake

From the dashboard Intake screen, select **+ New Intake** to create a domain-neutral `IntakeEvent` with `source_type="manual_upload"`. The form accepts optional subject, sender, and notes plus one or more local files through a native picker or drag and drop.

Files are uploaded sequentially through the existing IntakeArtifact API and preserved unchanged in local artifact storage. PDF candidates proceed through native text extraction; pages marked `partial` or `needs_ocr` then use selective local Tesseract OCR. Non-PDF files are safely preserved without invoking PDF processing. Per-file progress distinguishes upload, receipt, extraction, local OCR, readiness, unavailable processing, and partial failure.

Manual intake invokes no classifier, structured extractor, OpenAI adapter, or other external model. It is a local proof-of-concept input path built on the universal intake/artifact engine, not a production intake connector. Successfully stored originals are retained if later text processing fails.

## Dual-mode document processing

AdminFlow defaults to fully local deterministic processing:

```dotenv
AI_PROVIDER=stub
```

The local stub makes no external requests. It deterministically matches the application-owned `generic_office` taxonomy (`invoice`, `correspondence`, `form`, or `other`) and extracts ordinary labeled fields using the profile's constrained field definitions.

To use the existing OpenAI Responses API adapters instead:

```dotenv
AI_PROVIDER=openai
OPENAI_API_KEY=your-key-here
```

OpenAI mode still leaves taxonomy, field schemas, title construction, WorkItem creation, workflow state, and approve/reject routing under deterministic application control. Providers receive extracted text—not original artifact bytes.

`POST /document-extractions/{id}/process` runs the selected provider through classification and structured extraction, then atomically creates the derived records, WorkItem, initial transition, and pending human review for the `generic_office` profile. Repeating the request reuses its existing result. `GET /document-processing/config` exposes only non-secret provider readiness and available profile display information.

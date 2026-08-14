# AdminFlow

AdminFlow is a local-first administrative workflow engine. It is designed to turn incoming unstructured information into structured, reviewable work items while keeping workflow state and business rules deterministic.

> **Development bootstrap:** This repository currently contains a runnable backend, PostgreSQL persistence, health checks, migration infrastructure, domain-neutral intake/artifact foundations, native PDF extraction, selective OCR, and AI document classification. It is not a production-ready AdminFlow application and does not yet contain workflow behavior.

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

`DATABASE_URL` may be set to test another database. GitHub Actions starts a dedicated PostgreSQL service and enables the integration test automatically. AI classification tests inject a stub classifier and do not make external model calls.

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

# AdminFlow

AdminFlow is a local-first administrative workflow engine. It is designed to turn incoming unstructured information into structured, reviewable work items while keeping workflow state and business rules deterministic.

> **Development bootstrap:** This repository currently contains only the runnable backend, PostgreSQL connectivity, health checks, migration infrastructure, and tests. It is not a production-ready AdminFlow application and does not yet contain domain models or workflow behavior.

## Architecture

- **Backend:** Python 3.12 and FastAPI
- **Database:** PostgreSQL 16 through SQLAlchemy and psycopg
- **Migrations:** Alembic
- **AI layer:** replaceable local AI provider, to be added after the core engine
- **Workflow control:** deterministic application code
- **Local deployment:** Docker Compose on Linux

AI will interpret messy information. Deterministic code will validate data, control workflow state, create actions, and record history. Humans will review work when the workflow requires approval.

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

`DATABASE_URL` may be set to test another database. GitHub Actions starts a dedicated PostgreSQL service and enables the integration test automatically.

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

# AdminFlow Project Status

Status captured: 2026-08-15

## Current state

AdminFlow has a validated basic local dashboard on the `feature/basic-dashboard` branch, exposing the existing deterministic intake, document, WorkItem, and human-review APIs without adding another workflow path.

Last feature merge:

- PR #9 — `Add human review queue` — merged to `main`
- merge commit `31da87be5f36506ca321b7c0c574fbcf1e6147ab`
- final GitHub Actions validation: 139 passed, 0 skipped, and 0 failed
- all three new PostgreSQL human-review integration tests ran and passed
- all four existing PostgreSQL workflow integration tests also ran and passed
- Alembic upgraded successfully through `20260815_0009`

Estimated V1 completion on this validated feature branch: **~93%**.

**Next: Simple local/manual intake for the proof-of-concept.**

## Product architecture

AdminFlow is a **local-first administrative workflow engine**. The product boundary is the workflow engine, not the AI model.

Core rule:

> AI handles fuzzy interpretation. Deterministic application code owns workflow state, validation, rules, timers, permissions, and actions.

Current architecture is a modular monolith:

```text
Incoming source
     ↓
IntakeEvent
     ↓
IntakeArtifact metadata → PostgreSQL
Original artifact bytes → local artifact storage
     ↓
Native PDF extraction (pypdf)
     ↓
DocumentExtraction
     ↓
Page needs OCR?
   ┌───────┴────────┐
   No              Yes
   ↓                ↓
Keep native     Render page with
page text       pypdfium2
                    ↓
               Tesseract OCR
                    ↓
            derived pdf_text_ocr
            DocumentExtraction
                    ↓
          source_extraction_id lineage
                    ↓
          DocumentExtraction
                    ↓
      optional DocumentClassification
                    ↓
    DocumentStructuredExtraction
                    ↓
                WorkItem
                    ↓
        deterministic state engine
                    ↓
       immutable transition history
                    ↓
         human review queue
                    ↓
        local office dashboard
                    ↓
       future manual intake UI
```

### Backend

- Python 3.12
- FastAPI
- Pydantic
- SQLAlchemy
- psycopg
- PostgreSQL as the source of truth
- Alembic for schema migrations

### Storage

- Original artifact metadata is stored in PostgreSQL.
- Original artifact bytes are stored outside the database in local filesystem/object-style storage.
- Original source artifacts remain immutable.
- Derived extraction records are immutable.
- Derived OCR extractions retain lineage through `source_extraction_id`.
- Document classifications are immutable and retain direct lineage to the `DocumentExtraction` that was classified.
- Each classification stores the exact candidate taxonomy plus provider, model, and prompt-version metadata used to produce it.

### Native document reader

- Native PDF text extraction uses `pypdf`.
- Extraction preserves page boundaries.
- Page numbering is 1-based.
- Per-page character counts are retained.
- Pages without meaningful native text are marked `needs_ocr=true`.
- Combined `text_content` joins pages with exactly `"\n\n"`.
- Page separators are excluded from `character_count`.
- Password-protected and failed PDFs produce deterministic persisted statuses.

### Selective OCR

- OCR runs only for pages already marked `needs_ocr=true`.
- PDF pages are rasterized with `pypdfium2`.
- Default render resolution is 300 DPI.
- Tesseract 5 performs OCR.
- Default OCR language is English (`eng`).
- Native-text pages are copied unchanged into the derived result.
- OCR pages receive `text_source="ocr"`; retained native pages receive `text_source="native_text"`.
- Derived extraction method is `pdf_text_ocr`.
- Tesseract is invoked with a subprocess argument list and without `shell=True`.
- Temporary raster images are automatically cleaned up.
- OCR language, DPI, and timeout are configurable.

### AI document classification

- Classification consumes readable `DocumentExtraction.text_content` without modifying the extraction.
- The application supplies a candidate-label taxonomy with each classification request; the core engine does not hard-code industry-specific document types.
- Candidate label names are validated as unique before the AI provider is called.
- The AI provider is hidden behind a narrow `DocumentClassifier` interface.
- The first provider adapter uses the OpenAI Responses API with structured output.
- Document text is explicitly treated as untrusted data rather than instructions.
- AI output is validated before persistence.
- The selected label must exactly match one label in the application-supplied taxonomy.
- Confidence must be between 0 and 1.
- Provider failures return a sanitized API error and do not persist a classification.
- Missing AI configuration affects only classification requests; the deterministic intake/extraction/OCR foundation remains usable.
- Classification does not transition workflow state or trigger actions.

### AI structured data extraction

- Structured extraction consumes readable `DocumentExtraction.text_content` without modifying it.
- The application supplies a constrained field-definition contract; no industry fields are hard-coded.
- Supported V1 types are string, integer, number, boolean, ISO date, and string array.
- Optional classification context is accepted only after deterministic same-extraction lineage validation.
- The replaceable `DocumentStructuredExtractor` interface isolates the OpenAI Responses API adapter.
- Provider output is deterministically revalidated for exact keys, required values, strict types, real calendar dates, and string arrays before persistence.
- Immutable results retain extraction lineage, optional classification lineage, the exact field-schema snapshot, and provider metadata.
- Structured extraction does not create WorkItems, transition workflow state, or trigger actions.

### API / domain objects implemented

#### IntakeEvent

Represents a normalized incoming administrative event.

Implemented fields include source type, external ID, sender, recipient, subject, body text, received time, raw metadata, timestamps, and deterministic intake status.

Implemented API operations include create, list, and get-by-ID.

#### IntakeArtifact

Represents an original file attached to an IntakeEvent.

Implemented behavior includes file upload, metadata persistence, SHA-256 hashing, internal storage keys, artifact listing, metadata retrieval, and original-content retrieval.

#### DocumentExtraction

Represents immutable text derived from an IntakeArtifact.

Implemented extraction methods:

- `pdf_text`
- `pdf_text_ocr`

Implemented deterministic statuses include:

- `extracted`
- `partial`
- `needs_ocr`
- `password_required`
- `failed`

#### DocumentClassification

Represents one immutable classification of a readable `DocumentExtraction`.

Persisted fields include:

- source `document_extraction_id`
- exact candidate-label taxonomy snapshot
- provider name
- model name
- prompt version
- selected label
- confidence
- concise rationale
- timestamp

Implemented API operations include create, list-by-extraction, and get-by-ID.

#### DocumentStructuredExtraction

Represents one immutable, deterministically validated structured result derived from readable document text. It stores required `DocumentExtraction` lineage, optional same-extraction `DocumentClassification` lineage, JSONB field-schema and data snapshots, provider/model/prompt metadata, and a timestamp. Implemented API operations include create, list-by-extraction, and get-by-ID.

#### WorkflowDefinition

Represents an immutable, application-defined state graph. Definitions store exact JSONB state and transition snapshots and are validated for unique identifiers and edges, reachability, terminal-state behavior, and paths to completion. Cycles are allowed when a terminal path remains available.

#### WorkItem

Represents current deterministic workflow state with required IntakeEvent lineage and optional validated DocumentStructuredExtraction lineage. WorkItems begin in the definition initial state at version 1. Structured source data is copied exactly; callers cannot override it.

#### WorkItemTransition

Represents immutable chronological state history, including the initial creation record. State/version changes and history inserts occur atomically through the deterministic transition engine. Expected state and version values reject stale clients.

#### WorkItemReview

Represents the persisted human-review task and audit result for an exact WorkItem state and version. Pending reviews are created atomically when review-required states are entered. Approval or rejection maps to immutable WorkflowDefinition edges and resolves through the existing deterministic transition engine. Human corrections are retained as review snapshots without modifying immutable structured-extraction sources.

## Infrastructure and development environment

- Docker Compose development environment
- PostgreSQL container
- FastAPI backend container
- Persistent artifact volume
- Backend health endpoint
- PostgreSQL health endpoint
- GitHub Actions CI
- pytest test suite
- Alembic migration chain through `20260815_0009`

The local development stack has been run successfully on Ubuntu Linux.

## Completed feature slices

1. **Backend foundation**
   - FastAPI application
   - PostgreSQL connectivity
   - Docker Compose
   - health endpoints
   - pytest
   - GitHub Actions
   - Alembic baseline

2. **IntakeEvent**
   - normalized incoming-event model
   - create/list/get API
   - PostgreSQL persistence

3. **IntakeArtifact**
   - artifact metadata in PostgreSQL
   - original bytes in local storage
   - SHA-256 hashing
   - safe internal storage keys
   - upload/list/metadata/content API

4. **Native PDF document reader**
   - pypdf native text extraction
   - page-level results
   - deterministic extraction statuses
   - OCR eligibility marker

5. **Selective PDF OCR**
   - pypdfium2 page rasterization
   - Tesseract OCR
   - OCR only for `needs_ocr` pages
   - immutable derived extraction
   - extraction lineage
   - Docker and CI Tesseract installation

6. **AI document classification**
   - domain-neutral candidate taxonomy supplied per request
   - replaceable classifier interface
   - OpenAI structured-output adapter
   - deterministic validation of provider output
   - immutable `DocumentClassification` persistence and extraction lineage
   - create/list/get classification API
   - provider/model/prompt metadata retained
   - no workflow-state or action behavior added

7. **AI structured data extraction**
   - application-defined constrained field contracts
   - replaceable structured-extractor interface and OpenAI adapter
   - strict deterministic validation after provider output
   - immutable JSONB results with extraction and optional classification lineage
   - create/list/get API
   - no WorkItem, workflow-state, or action behavior

8. **WorkItem + deterministic workflow engine**
   - immutable validated WorkflowDefinition graphs
   - WorkItems with source lineage and current state
   - deterministic state transitions with optimistic concurrency guards
   - immutable chronological transition audit history
   - atomic current-state and history persistence
   - no AI state decisions, actions, timers, or human-approval behavior

9. **Human review / approval queue**
   - workflow states explicitly declare review requirements
   - deterministic approve/reject edge mapping
   - pending review creation within WorkItem transactions
   - oldest-first review queue and immutable review audit records
   - strict structured-data correction validation
   - row locking and stale-client guards during resolution
   - no AI review decisions, authentication, notifications, or frontend

10. **Basic frontend / dashboard**
   - FastAPI-served static HTML, CSS, and vanilla JavaScript
   - dashboard summaries derived from existing APIs
   - oldest-first review queue and schema-aware human review form
   - local PDF Blob preview with object-URL cleanup
   - read-only WorkItem lineage/history and IntakeEvent artifact views
   - no external frontend dependencies, upload UI, authentication, or AI calls

## Verification and test results

### Selective OCR baseline

Selective OCR was validated locally in Docker with the real Tesseract binary and PostgreSQL integration enabled:

```text
41 passed
0 skipped
```

GitHub Actions also passed after PR #5 was opened. The CI job successfully completed:

- container initialization
- Python setup
- Tesseract / English OCR dependency installation
- backend/test dependency installation
- Alembic migrations through `0005`
- full test run

### AI document classification

PR #6 was validated by GitHub Actions on feature commit `f512e393eb10c1d83f6e2cf6194172268354301f`.

The CI job successfully completed:

- PostgreSQL service initialization
- Python 3.12 setup
- Tesseract dependency installation
- backend/test dependency installation including the OpenAI SDK
- Alembic migration through `20260814_0006`
- full pytest suite, including classification unit tests and PostgreSQL classification integration tests

Classification tests use injected stub/fake providers and do not require an external model call in CI.

### AI structured data extraction

PR #7 was merged to `main` as `3f5ab13372fa8942c0460f2c9bc2b99bb7ad7b26` after final GitHub Actions validation passed with 86 tests, 0 skipped, and 0 failed. PostgreSQL integration tests executed and passed, including both structured-extraction integration tests, and Alembic upgraded through `20260814_0007`. Automated validation made no external OpenAI request.

### WorkItem + deterministic workflow engine

PR #8 was merged to `main` as `8c99a49549f58ae65fb0bc389aed746f39c6bb8f` after final GitHub Actions validation passed with 113 tests, 0 skipped, and 0 failed. All four PostgreSQL workflow integration tests executed and passed, and Alembic upgraded through `20260814_0008`. Workflow-state decisions remain deterministic and no human-review functionality was added.

### Human review / approval queue

PR #9 was merged to `main` as `31da87be5f36506ca321b7c0c574fbcf1e6147ab`. GitHub Actions tested the PR merge candidate against fresh PostgreSQL 16, ran Alembic from baseline through `20260815_0009`, and passed 139 tests with 0 skipped and 0 failed. All three new WorkItemReview PostgreSQL integration tests and all four existing workflow PostgreSQL integration tests passed. Deterministic approve/reject routing remained application-controlled; no AI review or workflow-state decisions were introduced.

### Basic frontend / dashboard

The feature branch adds a same-origin local office interface at `/app/`. Focused route/static tests passed 3 tests with 0 failures. The complete local suite passed 123 tests with 20 environment-dependent PostgreSQL/Tesseract tests skipped and 0 failures when external AI configuration was explicitly disabled. JavaScript syntax validation passed. GitHub Actions remains the PostgreSQL validation gate for the feature PR.

### Prior real-document OCR validation

A real `Condenser Pump Down` PDF was previously used to compare native extraction with an image-only OCR path. The original native-text PDF produced one page, 1,157 characters, `status="extracted"`, and `needs_ocr=false`. Tesseract recovered 1,152 characters from the image-only copy with 84.86% whole-string similarity to the native reference. The recovered text was readable and usable; visible header text, list numbering, whitespace, and layout accounted for accepted differences. Layout interpretation remains deferred to a later document-understanding layer.

### Live OpenAI end-to-end document validation

The first successful live OpenAI end-to-end document validation was completed on 2026-08-15 using `Condenser Pump Down - Image Only.pdf`.

The validation chain completed successfully:

1. An `IntakeEvent` was created.
2. The image-only PDF was uploaded as an immutable `IntakeArtifact`.
3. Native PDF extraction produced `extraction_method="pdf_text"`, `status="needs_ocr"`, and `character_count=0`.
4. Selective OCR produced extraction `ae652b56-d965-47e7-a08d-dee227a6a8f4`, derived from native extraction `b8421354-4a63-441c-ab0e-6088388a3c28`, with `extraction_method="pdf_text_ocr"`, `status="extracted"`, `page_count=1`, and `character_count=1152`.
5. Live OpenAI document classification produced immutable classification `2e0db52a-30dc-4734-860b-e97131bc2a31` using provider `openai`, model `gpt-5-mini`, and prompt version `document-classification-v1`. The selected result was `procedure` with confidence `0.99`.
6. Live OpenAI structured extraction produced immutable result `4264feb5-cfa3-4083-8da2-126fe42845a6` using provider `openai`, model `gpt-5-mini`, and prompt version `document-structured-extraction-v1`. Classification lineage was preserved and the structured data persisted successfully.
7. Extracted fields included `title`, `safety_precautions`, `ppe_items`, `procedure_steps`, `tools_or_equipment`, and `completion_note`.
8. Persistence was verified through successful classification and structured-extraction GET requests.

The OCR source contained minor recognition noise, but structured extraction correctly identified the meaningful procedure steps. The original artifact, native extraction, and OCR extraction remained immutable. AI did not control or alter workflow state, and no workflow transition was performed by AI.

## V1 progress estimate

| V1 capability | Weight | Status |
|---|---:|---|
| Backend / DB / Docker / migrations / testing | 10% | Done |
| Universal IntakeEvent | 5% | Done |
| Original-file / artifact storage | 8% | Done |
| Native PDF reader | 5% | Done |
| Selective OCR for scanned PDFs | 8% | Done |
| AI document classification | 10% | Done |
| AI structured data extraction | 12% | Done |
| WorkItem + deterministic workflow engine | 15% | Done |
| Human review / approval queue | 12% | Done |
| Basic frontend / dashboard | 8% | Done |
| First real intake connector | 4% | Not started |
| Pilot polish / configuration | 3% | Not started |

Current weighted completion on this feature branch: **~93%**.

## Not implemented yet

The repository does **not** yet contain:

- document layout/list interpretation
- local/manual intake user experience
- production intake connectors
- industry-specific workflow packs

## Next feature: Simple local/manual intake for the proof-of-concept

The next slice should add a simple local/manual intake user experience that feeds the existing universal `IntakeEvent` and `IntakeArtifact` engine. The dashboard does not mean a production intake connector exists, and no connector is marked complete.

## Handoff instructions for a new ChatGPT / Codex session

Use GitHub as the technical source of truth.

Start a new session with:

> Continue the AdminFlow project. GitHub repo `rwalk-chronos/Admin_Flow` is the source of truth. Read `AGENTS.md` and `PROJECT_STATUS.md`, inspect current `main`, and continue from the documented next feature. We use ChatGPT for architecture/review and Codex for implementation. Do not assume any uncommitted local work exists.

Before starting a feature, make sure local `main` matches `origin/main`, then create a feature branch from that clean state.

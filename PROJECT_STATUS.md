# AdminFlow Project Status

Status captured: 2026-08-14

## Current state

AdminFlow is at a clean feature boundary after the selective PDF OCR foundation was merged to `main`.

Last feature merge before this status file:

- `85aa8a1b6f271f7c61db9b1015d3334c532601aa` — `Add selective PDF OCR (#5)`

Estimated V1 completion: **~36%**.

**Next: AI document classification.**

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

## Infrastructure and development environment

- Docker Compose development environment
- PostgreSQL container
- FastAPI backend container
- Persistent artifact volume
- Backend health endpoint
- PostgreSQL health endpoint
- GitHub Actions CI
- pytest test suite
- Alembic migration chain through `20260814_0005`

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

## Verification and test results

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

### Real document validation

A real `Condenser Pump Down` PDF was used to validate both extraction paths.

Original PDF with native text:

```text
pages: 1
characters: 1157
status: extracted
needs_ocr: false
```

An image-only copy of the same document correctly produced no native text and was routed to OCR. Tesseract recovered:

```text
OCR characters: 1152
SequenceMatcher whole-string similarity to native reference: 84.86%
```

The recovered text was clearly readable and usable. Differences included visible header text found by OCR, list-numbering differences, and whitespace/layout differences. Layout/list interpretation is intentionally deferred to a later document-understanding layer.

## V1 progress estimate

| V1 capability | Weight | Status |
|---|---:|---|
| Backend / DB / Docker / migrations / testing | 10% | Done |
| Universal IntakeEvent | 5% | Done |
| Original-file / artifact storage | 8% | Done |
| Native PDF reader | 5% | Done |
| Selective OCR for scanned PDFs | 8% | Done |
| AI document classification | 10% | **Next** |
| AI structured data extraction | 12% | Not started |
| WorkItem + deterministic workflow engine | 15% | Not started |
| Human review / approval queue | 12% | Not started |
| Basic frontend / dashboard | 8% | Not started |
| First real intake connector | 4% | Not started |
| Pilot polish / configuration | 3% | Not started |

Current weighted completion: **~36%**.

## Not implemented yet

The repository does **not** yet contain:

- AI document classification
- AI structured field extraction
- document layout/list interpretation
- WorkItem domain model
- deterministic workflow/state engine
- human review/approval queue
- frontend/dashboard
- production intake connectors
- industry-specific workflow packs

## Next feature: AI document classification

The next slice should classify the readable text produced by `DocumentExtraction` without changing the document-ingestion foundation.

Desired flow:

```text
IntakeArtifact
     ↓
DocumentExtraction
(native and/or OCR text)
     ↓
AI document classification
     ↓
structured classification result
     ↓
future structured extraction / workflow routing
```

The classifier should follow existing architecture rules:

- AI performs interpretation only.
- AI output crosses the application boundary as validated structured data.
- Classification must not directly transition workflow state.
- The AI provider should remain replaceable behind a narrow interface.
- Core engine logic remains domain-neutral.
- Do not add workflow, frontend, connectors, or industry-specific behavior as part of the classification slice unless explicitly requested.

## Handoff instructions for a new ChatGPT / Codex session

Use GitHub as the technical source of truth.

Start a new session with:

> Continue the AdminFlow project. GitHub repo `rwalk-chronos/Admin_Flow` is the source of truth. Read `AGENTS.md` and `PROJECT_STATUS.md`, inspect current `main`, and continue from the documented next feature. We use ChatGPT for architecture/review and Codex for implementation. Do not assume any uncommitted local work exists.

Before starting a feature, make sure local `main` matches `origin/main`, then create a feature branch from that clean state.

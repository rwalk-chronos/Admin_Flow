from dataclasses import dataclass
from typing import BinaryIO, Protocol

from pypdf import PdfReader


PAGE_SEPARATOR = "\n\n"


@dataclass(frozen=True)
class PageReadResult:
    page_number: int
    text: str
    character_count: int
    needs_ocr: bool

    def as_dict(self) -> dict[str, int | str | bool]:
        return {
            "page_number": self.page_number,
            "text": self.text,
            "character_count": self.character_count,
            "needs_ocr": self.needs_ocr,
        }


@dataclass(frozen=True)
class DocumentReadResult:
    status: str
    page_count: int
    character_count: int
    text_content: str | None
    page_results: list[dict[str, int | str | bool]]
    error_message: str | None = None


class DocumentReader(Protocol):
    extraction_method: str

    def read(self, source: BinaryIO) -> DocumentReadResult: ...


class PdfTextReader:
    extraction_method = "pdf_text"

    def read(self, source: BinaryIO) -> DocumentReadResult:
        try:
            reader = PdfReader(source, strict=False)
            if reader.is_encrypted:
                try:
                    can_read_without_password = reader.decrypt("") != 0
                except Exception:
                    can_read_without_password = False
                if not can_read_without_password:
                    return DocumentReadResult(
                        status="password_required",
                        page_count=0,
                        character_count=0,
                        text_content=None,
                        page_results=[],
                    )

            pages = []
            for page_number, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                pages.append(
                    PageReadResult(
                        page_number=page_number,
                        text=text,
                        character_count=len(text),
                        needs_ocr=not bool(text.strip()),
                    )
                )

            meaningful_page_count = sum(not page.needs_ocr for page in pages)
            if meaningful_page_count == len(pages) and pages:
                status = "extracted"
            elif meaningful_page_count:
                status = "partial"
            else:
                status = "needs_ocr"

            character_count = sum(page.character_count for page in pages)
            return DocumentReadResult(
                status=status,
                page_count=len(pages),
                character_count=character_count,
                text_content=(
                    PAGE_SEPARATOR.join(page.text for page in pages)
                    if character_count
                    else None
                ),
                page_results=[page.as_dict() for page in pages],
            )
        except Exception as error:
            return DocumentReadResult(
                status="failed",
                page_count=0,
                character_count=0,
                text_content=None,
                page_results=[],
                error_message=_safe_error_message(error),
            )


def _safe_error_message(error: Exception) -> str:
    detail = " ".join(str(error).split())
    if not detail:
        detail = type(error).__name__
    return f"PDF extraction failed: {detail}"[:500]

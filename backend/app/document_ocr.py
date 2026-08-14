import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO, Protocol

import pypdfium2 as pdfium

from app.document_reader import PAGE_SEPARATOR


@dataclass(frozen=True)
class OcrProcessResult:
    status: str
    page_count: int
    character_count: int
    text_content: str | None
    page_results: list[dict[str, int | str | bool]]
    error_message: str | None = None


class OcrProcessingError(RuntimeError):
    pass


class OcrEngine(Protocol):
    def extract_text(self, image_path: Path) -> str | None: ...


class TesseractOcrEngine:
    def __init__(self, language: str, timeout_seconds: int) -> None:
        self.language = language
        self.timeout_seconds = timeout_seconds

    def extract_text(self, image_path: Path) -> str:
        command = [
            "tesseract",
            str(image_path),
            "stdout",
            "-l",
            self.language,
            "--oem",
            "1",
            "--psm",
            "3",
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise OcrProcessingError(
                f"Tesseract timed out after {self.timeout_seconds} seconds"
            ) from error
        except OSError as error:
            raise OcrProcessingError(
                _concise_error("Tesseract could not start", error)
            ) from error

        if completed.returncode != 0:
            detail = completed.stderr or completed.stdout or "unknown error"
            raise OcrProcessingError(
                _concise_error("Tesseract failed", RuntimeError(detail))
            )
        return completed.stdout or ""


class PdfOcrProcessor:
    extraction_method = "pdf_text_ocr"

    def __init__(self, engine: OcrEngine, dpi: int) -> None:
        self.engine = engine
        self.dpi = dpi

    def process(
        self,
        source: BinaryIO,
        source_page_count: int,
        source_page_results: list[dict],
    ) -> OcrProcessResult:
        try:
            return self._process(source, source_page_count, source_page_results)
        except Exception as error:
            return OcrProcessResult(
                status="failed",
                page_count=0,
                character_count=0,
                text_content=None,
                page_results=[],
                error_message=_concise_error("Selective OCR failed", error),
            )

    def _process(
        self,
        source: BinaryIO,
        source_page_count: int,
        source_page_results: list[dict],
    ) -> OcrProcessResult:
        pdf = pdfium.PdfDocument(source.read())
        try:
            self._validate_page_integrity(
                pdf_page_count=len(pdf),
                source_page_count=source_page_count,
                page_results=source_page_results,
            )
            derived_pages = []
            with TemporaryDirectory(prefix="adminflow-ocr-") as temporary_directory:
                temporary_root = Path(temporary_directory)
                for source_page in source_page_results:
                    page_number = source_page["page_number"]
                    if not source_page["needs_ocr"]:
                        derived_page = dict(source_page)
                        derived_page["text_source"] = "native_text"
                    else:
                        image_path = temporary_root / f"page-{page_number}.png"
                        self._render_page(pdf, page_number, image_path)
                        ocr_text = self.engine.extract_text(image_path) or ""
                        derived_page = {
                            "page_number": page_number,
                            "text": ocr_text,
                            "character_count": len(ocr_text),
                            "needs_ocr": not bool(ocr_text.strip()),
                            "text_source": "ocr",
                        }
                    derived_pages.append(derived_page)
        finally:
            pdf.close()

        meaningful_page_count = sum(
            not page["needs_ocr"] for page in derived_pages
        )
        if meaningful_page_count == len(derived_pages) and derived_pages:
            status = "extracted"
        elif meaningful_page_count:
            status = "partial"
        else:
            status = "needs_ocr"

        character_count = sum(page["character_count"] for page in derived_pages)
        return OcrProcessResult(
            status=status,
            page_count=len(derived_pages),
            character_count=character_count,
            text_content=(
                PAGE_SEPARATOR.join(page["text"] for page in derived_pages)
                if character_count
                else None
            ),
            page_results=derived_pages,
        )

    def _render_page(
        self, pdf: pdfium.PdfDocument, page_number: int, image_path: Path
    ) -> None:
        page = pdf[page_number - 1]
        try:
            bitmap = page.render(scale=self.dpi / 72)
            try:
                image = bitmap.to_pil()
                try:
                    image.save(image_path, format="PNG")
                finally:
                    image.close()
            finally:
                bitmap.close()
        finally:
            page.close()

    @staticmethod
    def _validate_page_integrity(
        pdf_page_count: int,
        source_page_count: int,
        page_results: list[dict],
    ) -> None:
        if pdf_page_count != source_page_count:
            raise OcrProcessingError(
                "Original PDF page count does not match the source extraction"
            )
        if len(page_results) != source_page_count:
            raise OcrProcessingError(
                "Source page results do not match the source page count"
            )

        page_numbers = [page.get("page_number") for page in page_results]
        if page_numbers != list(range(1, source_page_count + 1)):
            raise OcrProcessingError(
                "Source page numbering is invalid for the original PDF"
            )
        for page in page_results:
            if not isinstance(page.get("text"), str):
                raise OcrProcessingError("Source page text is invalid")
            if not isinstance(page.get("character_count"), int):
                raise OcrProcessingError("Source page character count is invalid")
            if page["character_count"] < 0:
                raise OcrProcessingError("Source page character count is invalid")
            if not isinstance(page.get("needs_ocr"), bool):
                raise OcrProcessingError("Source page OCR marker is invalid")


def _concise_error(prefix: str, error: Exception) -> str:
    detail = " ".join(str(error).split()) or type(error).__name__
    return f"{prefix}: {detail}"[:500]

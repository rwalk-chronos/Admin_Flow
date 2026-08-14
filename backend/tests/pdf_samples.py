from io import BytesIO

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def build_pdf(page_texts: list[str | None]) -> bytes:
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)

    for text in page_texts:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): font_reference}
                )
            }
        )
        content = DecodedStreamObject()
        if text is None:
            content.set_data(b"q 0 0 100 100 re f Q")
        else:
            escaped = (
                text.replace("\\", "\\\\")
                .replace("(", "\\(")
                .replace(")", "\\)")
            )
            content.set_data(
                f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1")
            )
        page[NameObject("/Contents")] = writer._add_object(content)

    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def build_encrypted_pdf(text: str, password: str = "secret") -> bytes:
    reader = PdfReader(BytesIO(build_pdf([text])))
    writer = PdfWriter()
    writer.append_pages_from_reader(reader)
    writer.encrypt(password)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()

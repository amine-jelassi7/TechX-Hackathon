from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.services.pdf_parser import MAX_BYTES, PDFParseError, parse_pdf


def pdf_with_text(text: str = "Product name: Sample SaaS\nTagline: Safe synthetic demo") -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    content = DecodedStreamObject()
    escaped = (
        text.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace("\n", ") Tj T* (")
    )
    content.set_data(f"BT /F1 12 Tf 30 700 Td ({escaped}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(content)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_pdf_extracts_text_fields_and_keeps_review_metadata():
    result = parse_pdf(
        pdf_with_text(
            "Product name: Sample SaaS\nTagline: Safe synthetic demo\nPrice: 120 TND\nContact: demo@example.test"
        )
    )
    assert result["fields"]["name"] == "Sample SaaS"
    assert result["fields"]["price"] == 120
    assert result["fields"]["contact"] == "demo@example.test"
    assert set(result["fields"]).issubset(result["needs_review"])
    assert all(0 < confidence <= 1 for confidence in result["confidence"].values())


def test_wrong_magic_and_oversize_are_rejected():
    with pytest.raises(PDFParseError, match="not a PDF"):
        parse_pdf(b"not a PDF")
    with pytest.raises(PDFParseError, match="5 MB"):
        parse_pdf(b"%PDF-" + b"x" * MAX_BYTES)


def test_scanned_or_blank_pdf_has_clear_message():
    with pytest.raises(PDFParseError, match="Scanned PDF not supported"):
        parse_pdf(pdf_with_text(""))


def test_encrypted_pdf_is_rejected():
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt("synthetic-password")
    output = BytesIO()
    writer.write(output)
    with pytest.raises(PDFParseError, match="Encrypted"):
        parse_pdf(output.getvalue())


def test_page_limit_is_enforced():
    writer = PdfWriter()
    for _ in range(31):
        writer.add_blank_page(width=612, height=792)
    output = BytesIO()
    writer.write(output)
    with pytest.raises(PDFParseError, match="30 pages"):
        parse_pdf(output.getvalue())


def test_extracted_text_limit_is_enforced():
    with pytest.raises(PDFParseError, match="extraction limit"):
        parse_pdf(pdf_with_text("A" * 200_001))

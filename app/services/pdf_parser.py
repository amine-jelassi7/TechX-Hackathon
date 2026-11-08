"""Safe text-only PDF intake. Upload bytes are never persisted."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

MAX_BYTES = 5 * 1024 * 1024
MAX_PAGES = 30
MAX_CHARS = 200_000


class PDFParseError(ValueError):
    pass


def parse_pdf(data: bytes) -> dict[str, Any]:
    if len(data) > MAX_BYTES:
        raise PDFParseError("PDF exceeds 5 MB")
    if not data.startswith(b"%PDF-"):
        raise PDFParseError("Uploaded file is not a PDF")
    try:
        from io import BytesIO

        from pypdf import PdfReader

        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise PDFParseError("Encrypted PDFs are not supported")
        if len(reader.pages) > MAX_PAGES:
            raise PDFParseError("PDF exceeds 30 pages")
        parts = []
        total = 0
        for page in reader.pages:
            part = page.extract_text() or ""
            total += len(part)
            if total > MAX_CHARS:
                raise PDFParseError("PDF text exceeds the extraction limit")
            parts.append(part)
        text = unicodedata.normalize("NFKC", "\n".join(parts))
        text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    except PDFParseError:
        raise
    except Exception as exc:
        raise PDFParseError("PDF could not be safely read") from exc
    if not text.strip():
        raise PDFParseError("Scanned PDF not supported, please fill the form")
    fields: dict[str, Any] = {}
    email = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)
    url = re.search(r"https?://[^\s]+", text)
    if email:
        fields["contact"] = email.group(0)
    if url:
        fields["website"] = url.group(0).rstrip(".,)")
    labels = r"product\s*name|nom\s+du\s+produit|tagline|slogan|problem(?:\s+solved)?|probl[eè]me|price|pricing|prix|contact|email|website|features|audience|integrations|differentiators"
    for key, label in {
        "name": r"product\s*name|nom\s+du\s+produit",
        "tagline": r"tagline|slogan",
        "description": r"problem(?:\s+solved)?|probl[eè]me",
    }.items():
        match = re.search(
            rf"(?im)^\s*(?:{label})\s*:\s*(.*?)(?=(?:{labels})\s*:|$)", text, re.DOTALL
        )
        if match:
            fields[key] = match.group(1).strip()[:500]
    price_match = re.search(
        r"(?i)(?:^|\s|[A-Za-z])(?:price|pricing|prix)\s*:\s*([0-9]{1,7}(?:[.,][0-9]{1,2})?)\s*(?:TND|USD|EUR)?",
        text,
    )
    if price_match:
        fields["price"] = float(price_match.group(1).replace(",", "."))
    for key, label in (
        ("features", "features"),
        ("audience", "audience"),
        ("integrations", "integrations"),
        ("differentiators", "differentiators"),
    ):
        match = re.search(rf"(?im){label}\s*:\s*(.*?)(?=(?:{labels})\s*:|$)", text, re.DOTALL)
        if match:
            fields[key] = [
                part.strip()[:200] for part in re.split(r"[,;|]", match.group(1)) if part.strip()
            ][:20]
    snippets = {key: str(value)[:160] for key, value in fields.items()}
    return {
        "fields": fields,
        "confidence": {key: 0.75 for key in fields},
        "source_snippets": snippets,
        "needs_review": list(fields),
        "sha256": hashlib.sha256(data).hexdigest(),
        "pages": len(reader.pages),
    }

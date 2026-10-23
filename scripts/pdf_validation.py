"""Conservative PDF structure checks; these do not verify article identity."""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


@dataclass(frozen=True)
class PDFValidation:
    valid: bool
    reason: str
    page_count: int = 0
    sha256: str = ""


def validate_pdf_bytes(payload: bytes) -> PDFValidation:
    """Check header, EOF marker, page tree and decoded page streams.

    Success establishes structural readability only. It cannot prove that a
    publisher returned the requested article or every intended page.
    """
    digest = hashlib.sha256(payload).hexdigest()
    if not re.match(rb"%PDF-\d\.\d", payload):
        return PDFValidation(False, "missing_pdf_header", sha256=digest)
    if not payload.rstrip().endswith(b"%%EOF"):
        return PDFValidation(False, "missing_pdf_eof", sha256=digest)
    try:
        reader = PdfReader(io.BytesIO(payload), strict=True)
        empty_password = False
        if reader.is_encrypted:
            if not reader.decrypt(""):
                return PDFValidation(False, "pdf_password_required", sha256=digest)
            empty_password = True
        page_count = len(reader.pages)
        if page_count < 1:
            return PDFValidation(False, "pdf_has_no_pages", sha256=digest)
        for page in reader.pages:
            _ = page.mediabox
            contents = page.get_contents()
            if contents is not None:
                contents.get_data()
        evidence = "pdf_structure_valid_empty_password" if empty_password else "pdf_structure_valid"
        return PDFValidation(True, evidence, page_count, digest)
    except Exception as exc:
        return PDFValidation(False, f"pdf_parse_error:{type(exc).__name__}", sha256=digest)


def validate_pdf(path: Path) -> PDFValidation:
    try:
        return validate_pdf_bytes(Path(path).read_bytes())
    except OSError as exc:
        return PDFValidation(False, f"pdf_file_error:{type(exc).__name__}")

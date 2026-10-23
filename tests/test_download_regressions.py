"""Offline checks for real PDF validation and resumable download behavior."""
from __future__ import annotations

import contextlib
import csv
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import download_accessible_fulltexts as download
from pdf_validation import validate_pdf_bytes


def pdf_bytes(password: str | None = None) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    if password is not None:
        writer.encrypt(user_password=password, owner_password="owner-password")
    target = io.BytesIO()
    writer.write(target)
    return target.getvalue()


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class PDFChecks(unittest.TestCase):
    def test_accepts_single_page_and_passwordless_permission_encryption(self):
        self.assertTrue(validate_pdf_bytes(pdf_bytes()).valid)
        self.assertTrue(validate_pdf_bytes(pdf_bytes("")).valid)
        self.assertEqual(validate_pdf_bytes(pdf_bytes()).page_count, 1)

    def test_rejects_password_required_truncation_and_disguised_html(self):
        self.assertFalse(validate_pdf_bytes(pdf_bytes("secret")).valid)
        self.assertFalse(validate_pdf_bytes(pdf_bytes()[:-20]).valid)
        self.assertFalse(validate_pdf_bytes(b"%PDF-1.7\nnot a real PDF\n%%EOF").valid)
        html = b"<!doctype html><html><body>Not a paper</body></html>"
        self.assertEqual(download.classify_payload(html, "application/pdf", "https://example.test/file.pdf"), "html")
        self.assertFalse(validate_pdf_bytes(html).valid)

    def test_invalid_replacement_cannot_destroy_existing_pdf(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "existing.pdf"
            original = pdf_bytes()
            path.write_bytes(original)
            with self.assertRaises(ValueError):
                download.atomic_bytes(path, b"<html>bad download</html>", check_pdf=True)
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(list(path.parent.glob("*.part")))


class ResumeChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def run_download(self, *flags: str):
        with contextlib.redirect_stdout(io.StringIO()):
            return download.main(["--run-root", str(self.root), "--delay", "0", *flags])

    def test_retry_only_failed_retains_success_and_default_then_handles_new(self):
        original = pdf_bytes()
        old_pdf = self.root / "old.pdf"
        old_pdf.write_bytes(original)
        candidates = [
            {"record_id": key, "title": f"Paper {key}", "pdf_url_candidate": f"https://example.test/{key}.pdf"}
            for key in ["A", "B", "C"]
        ]
        write_rows(self.root / "candidate_table.csv", candidates)
        write_rows(self.root / "download_logs/download_log.csv", [
            {"record_id": "A", "download_status": "success_pdf", "final_path": str(old_pdf), "custom_note": "keep"},
            {"record_id": "B", "download_status": "failed", "attempt_count": "2"},
        ])
        with patch.object(download, "fetch", return_value=(original, "application/pdf", "https://example.test/B.pdf")) as fetch:
            self.assertEqual(self.run_download("--retry-failed"), 0)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(fetch.call_args.args[0], "https://example.test/B.pdf")
        rows = {row["record_id"]: row for row in download.read_csv(self.root / "download_logs/download_log.csv")}
        self.assertEqual(set(rows), {"A", "B"})
        self.assertEqual(rows["A"]["custom_note"], "keep")
        self.assertEqual(rows["B"]["attempt_count"], "3")
        self.assertEqual(old_pdf.read_bytes(), original)
        history = download.read_csv(self.root / "download_logs/download_history.csv")
        self.assertTrue(any(row["record_id"] == "B" and row["download_status"] == "failed" for row in history))
        with patch.object(download, "fetch", return_value=(original, "application/pdf", "https://example.test/C.pdf")) as fetch:
            self.run_download()
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(fetch.call_args.args[0], "https://example.test/C.pdf")

    def test_stale_success_is_revalidated_instead_of_skipped(self):
        broken = self.root / "broken.pdf"
        broken.write_bytes(b"an HTML error saved under a pdf filename")
        write_rows(self.root / "candidate_table.csv", [{"record_id": "A", "title": "Paper A", "pdf_url_candidate": "https://example.test/A.pdf"}])
        write_rows(self.root / "download_logs/download_log.csv", [{"record_id": "A", "download_status": "success_pdf", "final_path": str(broken)}])
        with patch.object(download, "fetch", return_value=(pdf_bytes(), "application/pdf", "https://example.test/A.pdf")) as fetch:
            self.run_download("--retry-failed")
        self.assertEqual(fetch.call_count, 1)
        row = download.read_csv(self.root / "download_logs/download_log.csv")[0]
        self.assertTrue(download.validate_pdf(Path(row["final_path"])).valid)
        self.assertEqual(broken.read_bytes(), b"an HTML error saved under a pdf filename")

    def test_html_is_saved_pending_not_as_fulltext(self):
        write_rows(self.root / "candidate_table.csv", [{"record_id": "A", "title": "Paper A", "pdf_url_candidate": "https://example.test/A.pdf"}])
        payload = b"<html><body>Abstract and site navigation" + b"x" * 15000 + b"</body></html>"
        with patch.object(download, "fetch", return_value=(payload, "application/pdf", "https://example.test/A.pdf")):
            self.run_download()
        row = download.read_csv(self.root / "download_logs/download_log.csv")[0]
        self.assertEqual(row["download_status"], "saved_html_unverified")
        self.assertFalse(list(self.root.glob("downloaded_fulltexts/*.pdf")))
        self.assertTrue(Path(row["final_path"]).is_file())

    def test_changed_exclusion_is_respected_and_can_be_removed(self):
        row = {"record_id": "A", "title": "Paper A", "pdf_url_candidate": "https://example.test/A.pdf", "exclusion_reason_if_any": "outside_scope"}
        good = self.root / "existing.pdf"
        good.write_bytes(pdf_bytes())
        write_rows(self.root / "candidate_table.csv", [row])
        write_rows(self.root / "download_logs/download_log.csv", [{"record_id": "A", "download_status": "success_pdf", "final_path": str(good)}])
        with patch.object(download, "fetch") as fetch:
            self.run_download()
        fetch.assert_not_called()
        self.assertEqual(download.read_csv(self.root / "download_logs/download_log.csv")[0]["download_status"], "excluded")
        row["exclusion_reason_if_any"] = ""
        write_rows(self.root / "candidate_table.csv", [row])
        with patch.object(download, "fetch", return_value=(pdf_bytes(), "application/pdf", "https://example.test/A.pdf")):
            self.run_download()
        self.assertEqual(download.read_csv(self.root / "download_logs/download_log.csv")[0]["download_status"], "success_pdf")

    def test_duplicate_ids_stop_before_any_request(self):
        write_rows(self.root / "candidate_table.csv", [{"record_id": "same", "title": name} for name in ["A", "B"]])
        with patch.object(download, "fetch") as fetch, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.run_download()
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""Report results must match canonical state and never start a browser."""
from __future__ import annotations

import contextlib
import csv
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import generate_readable_report as report
import make_student_outputs as outputs
import prepare_harvest_run as prepare


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class Reports(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_web_content_and_exclusions_are_not_success_or_actionable_pdf(self):
        rows = [{"download_status": value} for value in ["success_pdf", "success_html", "saved_xml_unverified", "failed", "excluded"]]
        self.assertEqual(report.report_counts(rows), (1, 2, 3, 0))
        self.assertEqual(report.metric_text({"impact_factor": "9.9"}), "待核验")

    def test_canonical_even_empty_overrides_stale_alias(self):
        canonical = self.root / "download_logs/download_log.csv"
        write_rows(canonical, [{"record_id": "A", "download_status": "failed"}])
        write_rows(self.root / "总下载日志.csv", [{"record_id": "A", "download_status": "success_pdf"}])
        self.assertEqual(report.choose_log(self.root)[1][0]["download_status"], "failed")
        canonical.write_text("record_id,download_status\n", encoding="utf-8")
        self.assertEqual(report.choose_log(self.root)[1], [])
        self.assertEqual(outputs.choose_log_rows(self.root), [])

    def test_chinese_pdf_generation_without_process_launch(self):
        path = self.root / "report.pdf"
        rows = [{"id": "1", "title": "温室传感器研究示例（离线测试）", "year": "2026", "doi": "10.example/test", "download_status": "success_pdf"},
                {"id": "2", "title": "待核验网页示例", "download_status": "saved_html_unverified"}]
        with patch("subprocess.run", side_effect=AssertionError("No browser should start")), patch("subprocess.Popen", side_effect=AssertionError("No browser should start")):
            report.print_pdf(rows, "中文下载报告测试", None, path)
        reader = PdfReader(path)
        self.assertGreaterEqual(len(reader.pages), 1)
        text = "\n".join(page.extract_text() for page in reader.pages)
        self.assertIn("中文下载报告测试", text)
        self.assertIn("温室传感器", text)
        self.assertFalse(list(self.root.glob("*.part")))

    def test_stale_pdf_is_not_advertised_as_new_report(self):
        pdf = self.root / "old.pdf"
        pdf.write_bytes(b"old report")
        result = report.make_readme(self.root, "test", pdf, self.root / "new.html", [], pdf_current=False)
        self.assertIn("HTML 报告", result)
        self.assertNotIn("PDF 报告", result)

    def test_prepare_refuses_to_erase_existing_run(self):
        existing = self.root / "kept"
        existing.mkdir()
        data = existing / "candidate_table.csv"
        data.write_text("valuable user records", encoding="utf-8")
        argv = ["prepare_harvest_run", "--topic", "test", "--output-root", str(self.root), "--run-name", "kept"]
        with patch.object(sys, "argv", argv), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                prepare.main()
        self.assertEqual(data.read_text(), "valuable user records")


if __name__ == "__main__":
    unittest.main()

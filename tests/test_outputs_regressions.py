"""Offline regressions for priorities, conservative status labels, and resume."""

from __future__ import annotations

import contextlib
import csv
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rank = load_script("rank_candidates")
outputs = load_script("make_student_outputs")
organizer = load_script("organize_final_outputs")


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def run(module, root: Path) -> None:
    with patch.object(sys, "argv", [module.__name__, "--run-root", str(root)]), contextlib.redirect_stdout(io.StringIO()):
        module.main()


def ids(root: Path, filename: str) -> set[str]:
    return {row.get("序号") or row.get("record_id") for row in read_csv(root / filename)}


class OutputRegressions(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_priority_and_exclusions_are_independent_of_download_status(self) -> None:
        candidates = [
            {"record_id": "H", "title": "high relevant", "priority": "high", "keyword_relevance_score": "5"},
            {"record_id": "M", "title": "medium relevant", "priority": "medium", "keyword_relevance_score": "3"},
            {"record_id": "L", "title": "low relevant but downloaded", "priority": "low", "keyword_relevance_score": "1"},
            {"record_id": "X", "title": "high score but excluded", "priority": "high", "exclusion_reason_if_any": "outside_scope"},
        ]
        write_csv(self.root / "candidate_table.csv", candidates)
        original_candidate_bytes = (self.root / "candidate_table.csv").read_bytes()
        # A stale report alias must not mask the current resumable log.
        write_csv(self.root / "总下载日志.csv", [{"record_id": "H", "download_status": "success_pdf"}])
        write_csv(self.root / "download_logs" / "download_log.csv", [
            {"record_id": "H", "download_status": "inaccessible", "failure_reason": "http_403"},
            {"record_id": "M", "download_status": "saved_html_unverified"},
            {"record_id": "L", "download_status": "success_pdf"},
            {"record_id": "X", "download_status": "excluded", "failure_reason": "outside_scope"},
        ])

        run(outputs, self.root)
        self.assertEqual(ids(self.root, "高优先级文献.csv"), {"H"})
        self.assertEqual(ids(self.root, "中优先级文献.csv"), {"M"})
        self.assertEqual(ids(self.root, "低优先级文献.csv"), {"L", "X"})
        self.assertEqual(ids(self.root, "已下载文献清单.csv"), {"L"})
        self.assertEqual(ids(self.root, "待处理文献清单.csv"), {"H", "M"})
        table = {row["序号"]: row for row in read_csv(self.root / "候选文献总表.csv")}
        self.assertEqual(table["X"]["优先级"], "高")
        self.assertEqual(table["X"]["排除原因"], "outside_scope")
        self.assertEqual(table["H"]["下载状态代码"], "inaccessible")
        self.assertIn("待核验", table["M"]["全文状态"])
        self.assertEqual((self.root / "candidate_table.csv").read_bytes(), original_candidate_bytes)

    def test_legacy_and_current_saved_web_content_is_never_verified_fulltext(self) -> None:
        statuses = ["success_html", "success_xml", "success_binary", "saved_html_unverified", "saved_xml_unverified"]
        write_csv(self.root / "candidate_table.csv", [
            {"record_id": str(index), "title": status, "priority": "medium", "download_status": status}
            for index, status in enumerate(statuses)
        ])
        # The old output script swept arbitrary web files into 已下载全文.
        (self.root / "landing.html").write_text("a landing page, not a paper", encoding="utf-8")
        (self.root / "metadata.xml").write_text("<metadata/>", encoding="utf-8")
        run(outputs, self.root)
        run(organizer, self.root)
        self.assertEqual(ids(self.root, "已下载文献清单.csv"), set())
        self.assertEqual(len(read_csv(self.root / "待处理文献清单.csv")), len(statuses))
        self.assertTrue(all("待核验" in row["全文状态"] for row in read_csv(self.root / "候选文献总表.csv")))
        self.assertTrue(all(row["是否下载成功"] == "否" for row in read_csv(self.root / "文章地址总表.csv")))
        self.assertEqual(list((self.root / "已下载全文").iterdir()), [])

    def test_review_and_access_status_do_not_bias_keyword_relevance(self) -> None:
        base = {"title": "Greenhouse sensor study", "article_type_guess": "research article"}
        review = {**base, "title": "Greenhouse sensor review", "article_type_guess": "review"}
        accessible = {**base, "pdf_url_candidate": "https://example.test/paper.pdf", "open_access_status_if_detectable": "open"}
        scores = [rank.score_row(dict(row), ["greenhouse", "sensor"], [], []) for row in [base, review, accessible]]
        self.assertEqual([row["keyword_relevance_score"] for row in scores], ["4", "4", "4"])
        self.assertTrue(all(row["priority"] == "high" for row in scores))
        self.assertEqual(scores[1]["article_type_guess"], "review")

    def test_repeated_organize_preserves_resume_inputs_metadata_and_user_files(self) -> None:
        config = {"include_terms": ["greenhouse", "sensor"], "secondary_terms": ["controller"], "exclude_terms": []}
        (self.root / "config.json").write_text(json.dumps(config), encoding="utf-8")
        write_csv(self.root / "candidate_table.csv", [
            {"record_id": "H", "title": "Greenhouse sensor review", "quality_notes": "keep my notes"},
            {"record_id": "M", "title": "Greenhouse system", "quality_notes": "manual comparison"},
            {"record_id": "L", "title": "Controller guide", "quality_notes": "not an IF assessment"},
            {"record_id": "X", "title": "Greenhouse sensor", "exclusion_reason_if_any": "manual_exclusion"},
        ])
        write_csv(self.root / "download_logs" / "download_log.csv", [
            {"record_id": "H", "download_status": "failed"},
        ])
        (self.root / "downloaded_fulltexts").mkdir()
        kept_file = self.root / "downloaded_fulltexts" / "keep.pdf"
        kept_file.write_bytes(b"existing user attachment")
        internal = self.root / organizer.INTERNAL_DIR
        internal.mkdir()
        personal = internal / "personal-note.md"
        personal.write_text("keep this note", encoding="utf-8")
        # Even a name collision must not overwrite a user's existing file.
        collision = internal / "config.json"
        collision.write_text("personal content", encoding="utf-8")

        run(rank, self.root)
        run(outputs, self.root)
        original_inputs = {name: (self.root / name).read_bytes() for name in ["config.json", "candidate_table.csv", "download_logs/download_log.csv"]}
        run(organizer, self.root)
        snapshots = {path.name: path.read_bytes() for path in internal.iterdir() if path.is_file()}
        run(organizer, self.root)
        self.assertEqual({path.name: path.read_bytes() for path in internal.iterdir() if path.is_file()}, snapshots)
        for name, expected in original_inputs.items():
            self.assertEqual((self.root / name).read_bytes(), expected)
        self.assertEqual(personal.read_text(encoding="utf-8"), "keep this note")
        self.assertEqual(collision.read_text(encoding="utf-8"), "personal content")
        self.assertEqual(kept_file.read_bytes(), b"existing user attachment")
        self.assertTrue((self.root / "download_logs").is_dir())
        self.assertTrue((self.root / "downloaded_fulltexts").is_dir())

        # Direct commands remain usable after organization; generated schema
        # columns survive even when the hand-created input omitted them.
        run(rank, self.root)
        run(outputs, self.root)
        run(organizer, self.root)
        self.assertEqual(ids(self.root, "high_priority.csv"), ids(self.root, "高优先级文献.csv"))
        self.assertEqual(ids(self.root, "medium_priority.csv"), ids(self.root, "中优先级文献.csv"))
        self.assertEqual(ids(self.root, "low_priority.csv"), ids(self.root, "低优先级文献.csv"))
        candidates = {row["record_id"]: row for row in read_csv(self.root / "candidate_table.csv")}
        self.assertEqual(candidates["H"]["quality_notes"], "keep my notes")
        self.assertEqual(candidates["X"]["exclusion_reason_if_any"], "manual_exclusion")
        self.assertEqual(candidates["H"]["priority"], "high")
        self.assertEqual(candidates["M"]["priority"], "medium")
        self.assertEqual(candidates["L"]["priority"], "low")


if __name__ == "__main__":
    unittest.main()

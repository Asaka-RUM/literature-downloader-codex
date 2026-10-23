#!/usr/bin/env python3
"""Create student-friendly Chinese literature tables.

This script merges download status and verified journal metrics into every
paper-listing output. `期刊指标.csv`/`journal_metrics.csv` may exist as a
background source, but students should read the merged tables instead.
"""

from __future__ import annotations

import argparse
import csv
import shutil
from pathlib import Path


METRIC_COLUMNS = ["影响因子", "JCR分区", "指标年份", "指标来源", "索引情况"]
OUTPUT_COLUMNS = [
    "序号",
    "题名",
    "年份",
    "期刊或来源",
    "优先级",
    "关键词相关度",
    "排除原因",
    *METRIC_COLUMNS,
    "DOI",
    "文章地址",
    "全文状态",
    "下载状态代码",
    "文件或原因",
    "下一步建议",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def norm(value: object) -> str:
    return " ".join(str(value or "").casefold().strip().split())


def choose_candidate_rows(run_root: Path) -> list[dict[str, str]]:
    for name in ["candidate_table.csv", "候选文献总表.csv"]:
        if (run_root / name).exists():
            return read_csv(run_root / name)
    return []


def choose_log_rows(run_root: Path) -> list[dict[str, str]]:
    canonical = run_root / "download_logs" / "download_log.csv"
    if canonical.exists():
        return read_csv(canonical)
    paths = [
        # This is the downloader's current, resumable state. Root aliases and
        # combined reports may describe an earlier run.
        run_root / "download_logs" / "download_log.csv",
        run_root / "download_logs" / "下载日志.csv",
        run_root / "总下载日志.csv",
        run_root / "combined_download_log.csv",
        run_root / "下载日志.csv",
        run_root / "download_log.csv",
    ]
    for path in paths:
        rows = read_csv(path)
        if rows:
            return rows
    return []


def read_metrics(run_root: Path) -> dict[str, dict[str, str]]:
    metrics: dict[str, dict[str, str]] = {}
    for path in [run_root / "期刊指标.csv", run_root / "journal_metrics.csv"]:
        for row in read_csv(path):
            journal = row.get("期刊或来源") or row.get("journal") or row.get("source") or row.get("venue")
            if journal:
                metrics[norm(journal)] = row
    return metrics


def get_metric(row: dict[str, str], metric_row: dict[str, str], zh_key: str, en_key: str) -> str:
    return row.get(zh_key) or row.get(en_key) or metric_row.get(zh_key) or metric_row.get(en_key) or "待核验"


def status_text(status: str) -> str:
    return {
        "success_pdf": "已下载 PDF",
        "saved_html_unverified": "已保存网页（全文待核验）",
        "saved_xml_unverified": "已保存 XML（全文待核验）",
        "success_html": "已保存网页（旧记录，全文待核验）",
        "success_xml": "已保存 XML（旧记录，全文待核验）",
        "success_binary": "已保存文件（旧记录，格式待核验）",
        "inaccessible": "平台限制直连下载",
        "metadata_only": "只找到题录",
        "broken_link": "链接失效",
        "failed": "尝试失败",
        "excluded": "已排除",
        "pending": "待处理",
        "rate_limited": "请求受限，稍后重试",
    }.get(status, status or "待处理")


def next_step(status: str, reason: str) -> str:
    if status == "success_pdf":
        return "已拿到 PDF，按相关度选择阅读顺序；核对题名和 DOI"
    if status in {"saved_html_unverified", "saved_xml_unverified", "success_html", "success_xml", "success_binary"}:
        return "保存内容尚未确认是完整论文，先核验题名、DOI 和全文，再寻找 PDF"
    if status == "excluded":
        return "查看排除原因；如需重新纳入，修改候选表后重新排序"
    if reason in {"http_403", "paywall_or_login_detected"} or status == "inaccessible":
        return "用学校图书馆/VPN；不行就馆际互借或联系作者"
    if reason == "html_stub_not_fulltext":
        return "只拿到跳转页/占位页；用 DOI 走学校库或人工打开出版社页"
    if status == "broken_link":
        return "用 DOI 重新打开出版社页面"
    return "人工复查 DOI、作者主页、机构仓储、Unpaywall"


def merge_candidate_and_log(candidate_rows: list[dict[str, str]], log_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    logs_by_id: dict[str, dict[str, str]] = {}
    logs_by_doi: dict[str, dict[str, str]] = {}
    for row in log_rows:
        row_id = row.get("id") or row.get("record_id") or ""
        doi = norm(row.get("doi") or row.get("DOI"))
        if row_id:
            logs_by_id[row_id] = row
        if doi:
            logs_by_doi[doi] = row
    if not candidate_rows:
        candidate_rows = log_rows
    merged = []
    for index, row in enumerate(candidate_rows, start=1):
        row_id = row.get("record_id") or row.get("id") or row.get("序号") or str(index)
        doi = norm(row.get("doi") or row.get("DOI"))
        log = logs_by_id.get(row_id) or logs_by_doi.get(doi) or {}
        item = dict(row)
        for key in ["status", "download_status", "file", "final_path", "reason", "failure_reason"]:
            if log.get(key):
                item[key] = log[key]
        log_status = log.get("download_status") or log.get("status")
        if log_status:
            item["download_status"] = log_status
            item["下载状态代码"] = log_status
        if log.get("final_url") and not item.get("landing_page_url"):
            item["landing_page_url"] = log["final_url"]
        item["id"] = row_id
        merged.append(item)
    return merged


def row_status(row: dict[str, str]) -> str:
    status = row.get("下载状态代码") or row.get("download_status") or row.get("status") or row.get("下载状态") or ""
    if status:
        return status
    # Compatibility when the original English candidate table is unavailable.
    # Legacy saved HTML/XML must remain unverified rather than become success.
    return {
        "已下载 PDF": "success_pdf",
        "已保存网页全文": "success_html",
        "已保存 XML 全文": "success_xml",
        "已排除": "excluded",
        "待处理": "pending",
    }.get(row.get("全文状态", ""), "")


def row_priority(row: dict[str, str]) -> str:
    value = norm(row.get("priority") or row.get("优先级"))
    return {"high": "高", "medium": "中", "low": "低", "高": "高", "中": "中", "低": "低"}.get(value, "未排序")


def convert_rows(rows: list[dict[str, str]], metrics: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    converted = []
    for index, row in enumerate(rows, start=1):
        journal = row.get("期刊或来源") or row.get("journal") or row.get("source") or ""
        metric_row = metrics.get(norm(journal), {})
        status = row_status(row)
        reason = row.get("reason") or row.get("failure_reason") or row.get("文件或原因") or ""
        exclusion = row.get("exclusion_reason_if_any") or row.get("排除原因") or (reason if status == "excluded" else "")
        file_value = row.get("文件或原因") or row.get("file") or row.get("final_path") or reason
        file_or_reason = Path(file_value).name if file_value and ("/" in file_value or "\\" in file_value) else file_value
        converted.append(
            {
                "序号": row.get("序号") or row.get("id") or row.get("record_id") or str(index),
                "题名": row.get("题名") or row.get("title") or row.get("paper") or "",
                "年份": row.get("年份") or row.get("year") or "",
                "期刊或来源": journal,
                "优先级": row_priority(row),
                "关键词相关度": row.get("keyword_relevance_score") or row.get("关键词相关度") or "",
                "排除原因": exclusion,
                "影响因子": get_metric(row, metric_row, "影响因子", "impact_factor"),
                "JCR分区": get_metric(row, metric_row, "JCR分区", "jcr_quartile"),
                "指标年份": get_metric(row, metric_row, "指标年份", "metric_year"),
                "指标来源": get_metric(row, metric_row, "指标来源", "metric_source"),
                "索引情况": get_metric(row, metric_row, "索引情况", "indexing"),
                "DOI": row.get("DOI") or row.get("doi") or "",
                "文章地址": row.get("文章地址") or row.get("article_url") or row.get("landing_page_url") or row.get("url") or "",
                "全文状态": status_text(status),
                "下载状态代码": status,
                "文件或原因": file_or_reason,
                "下一步建议": "查看排除原因；如需重新纳入，修改候选表后重新排序" if exclusion else next_step(status, reason),
            }
        )
    return converted


def ensure_fulltext_alias(run_root: Path, rows: list[dict[str, str]]) -> None:
    fulltext_dir = run_root / "已下载全文"
    fulltext_dir.mkdir(exist_ok=True)
    # Never sweep arbitrary root HTML/XML/PDFs into the verified fulltext folder.
    # The downloader normally creates its own alias. Repair a missing PDF alias
    # only when the corresponding log marks success and the file still parses.
    for row in rows:
        if row_status(row) != "success_pdf":
            continue
        value = row.get("final_path") or row.get("file") or ""
        if not value:
            continue
        path = Path(value)
        if not path.is_absolute():
            path = run_root / path
        target = fulltext_dir / path.name
        if path.is_file() and path.suffix.lower() == ".pdf" and not target.exists():
            from pdf_validation import validate_pdf

            if validate_pdf(path).valid:
                shutil.copyfile(path, target)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成学生可读的中文文献清单，并把期刊指标合并到每一行。")
    parser.add_argument("--run-root", required=True)
    args = parser.parse_args()

    run_root = Path(args.run_root).expanduser().resolve()
    merged = merge_candidate_and_log(choose_candidate_rows(run_root), choose_log_rows(run_root))
    rows = convert_rows(merged, read_metrics(run_root))
    if not rows:
        raise SystemExit(f"没有找到可转换的文献表或下载日志：{run_root}")

    write_csv(run_root / "候选文献总表.csv", rows)
    write_csv(run_root / "高优先级文献.csv", [row for row in rows if row["优先级"] == "高" and not row["排除原因"]])
    write_csv(run_root / "中优先级文献.csv", [row for row in rows if row["优先级"] == "中" and not row["排除原因"]])
    write_csv(run_root / "低优先级文献.csv", [row for row in rows if row["优先级"] == "低" or row["排除原因"]])
    write_csv(run_root / "已下载文献清单.csv", [row for row in rows if row["下载状态代码"] == "success_pdf"])
    write_csv(run_root / "待处理文献清单.csv", [row for row in rows if row["下载状态代码"] != "success_pdf" and not row["排除原因"]])
    ensure_fulltext_alias(run_root, merged)
    print(f"已生成学生可读文献清单：{run_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

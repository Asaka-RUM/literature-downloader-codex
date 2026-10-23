#!/usr/bin/env python3
"""Add readable indexes while retaining canonical inputs and resumable logs.

Small technical files get non-destructive snapshots in the internal directory.
Canonical files and download directories always remain in the run root.
"""

from __future__ import annotations

import argparse
import csv
import filecmp
import shutil
from pathlib import Path


INTERNAL_DIR = "内部数据_一般不用打开"

ENGLISH_NAMES = {
    "candidate_table.csv",
    "config.json",
    "dedup_manifest.csv",
    "harvest_summary.md",
    "high_priority.csv",
    "journal_metrics.csv",
    "low_priority.csv",
    "medium_priority.csv",
    "search_plan.md",
    "combined_download_log.csv",
    "download_log.csv",
}

TECHNICAL_CHINESE_NAMES = {
    "配置文件.json",
    "期刊指标.csv",
    "去重清单.csv",
    "下载汇总.md",
    "下载日志.csv",
    "总下载日志.csv",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def status_success(value: str) -> str:
    return "是" if value in {"success_pdf", "已下载 PDF"} else "否"


def article_url(row: dict[str, str], logs_by_id: dict[str, dict[str, str]]) -> str:
    row_id = row.get("序号", "")
    log = logs_by_id.get(row_id, {})
    return (
        row.get("文章地址")
        or row.get("地址")
        or log.get("url")
        or log.get("final_url")
        or log.get("access_route_used")
        or (f"https://doi.org/{row.get('DOI')}" if row.get("DOI") else "")
    )


def make_article_address_table(run_root: Path) -> None:
    rows = read_csv(run_root / "候选文献总表.csv")
    logs = (
        read_csv(run_root / "download_logs" / "download_log.csv")
        or read_csv(run_root / "总下载日志.csv")
        or read_csv(run_root / "下载日志.csv")
    )
    logs_by_id = {row.get("id", "") or row.get("record_id", ""): row for row in logs}
    fields = ["序号", "题名", "优先级", "排除原因", "文章地址", "是否下载成功", "全文状态", "DOI", "下一步建议"]
    out = []
    for row in rows:
        out.append(
            {
                "序号": row.get("序号", ""),
                "题名": row.get("题名", ""),
                "优先级": row.get("优先级", ""),
                "排除原因": row.get("排除原因", ""),
                "文章地址": article_url(row, logs_by_id),
                "是否下载成功": status_success(row.get("下载状态代码") or row.get("全文状态", "")),
                "全文状态": row.get("全文状态", ""),
                "DOI": row.get("DOI", ""),
                "下一步建议": row.get("下一步建议", ""),
            }
        )
    write_csv(run_root / "文章地址总表.csv", fields, out)


def snapshot_internal_files(run_root: Path) -> None:
    internal = run_root / INTERNAL_DIR
    internal.mkdir(exist_ok=True)
    for item in list(run_root.iterdir()):
        if item.name == INTERNAL_DIR:
            continue
        if item.is_file() and (item.name in ENGLISH_NAMES or item.name in TECHNICAL_CHINESE_NAMES):
            target = internal / item.name
            index = 1
            # Keep previous snapshots and unrelated user files intact. Repeated
            # organization of identical content does not create extra copies.
            while target.exists():
                if target.is_file() and filecmp.cmp(item, target, shallow=False):
                    break
                index += 1
                target = internal / f"{item.stem}_{index:03d}{item.suffix}"
            else:
                shutil.copy2(item, target)


def move_internal_files(run_root: Path) -> None:
    """Compatibility name: never move files needed by direct resume commands."""
    snapshot_internal_files(run_root)


def write_explainer(run_root: Path) -> None:
    text = """这个文件夹怎么用

最推荐先打开：
1. 下载报告.html（生成 PDF 时也可打开 下载报告.pdf）
   给人看的总结报告，包含下载结果、期刊指标、待处理文献。

2. 已下载全文
   格式校验通过的 PDF；阅读时仍需核对题名和 DOI，不能仅凭下载成功认定文章适合引用。

3. 文章地址总表.csv
   每篇文献的题名、文章地址、是否下载成功、下一步建议。

4. 候选文献总表.csv
   全部候选文献，保留相关度优先级、排除原因和全文状态。期刊指标未查证时显示“待核验”。

5. 已下载文献清单.csv
   下载状态为 success_pdf 的文献。下载成功不会提高相关度优先级。

6. 待处理文献清单.csv
   尚未取得 PDF 的未排除文献。已保存网页/XML 仍需核实是否包含完整论文。

7. 高优先级文献.csv / 中优先级文献.csv / 低优先级文献.csv
   按关键词相关度分组，不代表文章质量或期刊指标。低优先级表同时保留已排除记录及原因。

8. 待核验内容
   保存的网页/XML，不能直接算作已获得全文。

一般不用打开：
内部数据_一般不用打开
   这里保存技术文件的快照；多次整理不会覆盖已有快照或个人文件。

继续运行时请保留根目录中的 config.json、candidate_table.csv、download_logs/、downloaded_fulltexts/。
它们是排序、下载续跑和重试命令使用的原始数据，不会被整理步骤移动或删除。

说明：
没有下载成功不代表文献不存在，通常是出版社限制自动直连下载。请优先用 DOI 在学校图书馆或机构 VPN 中打开。
"""
    (run_root / "00_先看我_文件说明.txt").write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="整理文献下载输出目录，让普通用户更容易看懂。")
    parser.add_argument("--run-root", required=True)
    args = parser.parse_args()

    run_root = Path(args.run_root).expanduser().resolve()
    make_article_address_table(run_root)
    write_explainer(run_root)
    move_internal_files(run_root)
    print(f"已整理输出目录：{run_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Generate a Chinese, human-readable literature download report.

The script reads common CSV logs produced by this skill and writes:
- 下载报告.html
- README_先看我.md
- 下载报告.pdf with --pdf, using ReportLab locally without any browser

If `journal_metrics.csv` exists in the run folder, the report will show
impact factor/quartile/source fields. Metrics must be verified by the agent
or user before use; the script does not invent impact factors.
"""

from __future__ import annotations

import argparse
import csv
import html
import shutil
import sys
import tempfile
from pathlib import Path


STATUS_ZH = {
    "success_pdf": "PDF 结构已校验；身份待核验",
    "saved_html_unverified": "已保存网页；内容待核验",
    "saved_xml_unverified": "已保存 XML；内容待核验",
    "success_html": "旧日志网页；内容待核验",
    "success_xml": "旧日志 XML；内容待核验",
    "metadata_only": "只找到题录",
    "inaccessible": "平台限制",
    "broken_link": "链接失效",
    "rate_limited": "被限流",
    "excluded": "已排除",
    "failed": "尝试失败",
}


REASON_ZH = {
    "http_401": "需要登录或机构权限",
    "http_402": "需要付费或订阅",
    "http_403": "平台拒绝直连下载，建议学校库/VPN/馆际互借",
    "http_404": "链接不存在或已变更",
    "http_410": "链接已失效",
    "paywall_or_login_detected": "检测到登录页或付费墙",
    "no_candidate_url": "没有找到可尝试的全文链接",
    "HTTPError": "网页请求失败，需要人工复查入口",
    "html_stub_not_fulltext": "只拿到跳转页/占位页，不是真正全文",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def choose_log(run_root: Path) -> tuple[Path | None, list[dict[str, str]]]:
    canonical = run_root / "download_logs" / "download_log.csv"
    if canonical.exists():
        return canonical, read_csv(canonical)
    candidates = [
        run_root / "download_logs" / "下载日志.csv",
        run_root / "总下载日志.csv",
        run_root / "combined_download_log.csv",
        run_root / "下载日志.csv",
        run_root / "download_log.csv",
    ]
    for path in candidates:
        rows = read_csv(path)
        if rows:
            return path, rows
    return None, []


def choose_candidate_rows(run_root: Path) -> list[dict[str, str]]:
    for path in [run_root / "candidate_table.csv", run_root / "候选文献总表.csv"]:
        if path.exists():
            return read_csv(path)
    return []


def normalize_key(value: object) -> str:
    return " ".join(str(value or "").casefold().strip().split())


def normalize_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    best: dict[str, dict[str, str]] = {}
    for index, row in enumerate(rows, start=1):
        row_id = row.get("id") or row.get("record_id") or str(index)
        row["id"] = row_id
        old = best.get(row_id)
        row_status = status(row)
        old_status = status(old) if old else ""
        if old is None or old_status != "success_pdf" or row_status == "success_pdf":
            best[row_id] = row
    return [best[key] for key in sorted(best, key=lambda value: int(value) if value.isdigit() else 999999)]


def merge_candidate_and_log(candidate_rows: list[dict[str, str]], log_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    logs_by_id: dict[str, dict[str, str]] = {}
    logs_by_doi: dict[str, dict[str, str]] = {}
    for row in normalize_rows(log_rows):
        row_id = row.get("id") or row.get("record_id") or ""
        doi = normalize_key(row.get("doi") or row.get("DOI"))
        if row_id:
            logs_by_id[row_id] = row
        if doi:
            logs_by_doi[doi] = row
    if not candidate_rows:
        return log_rows
    merged = []
    for index, row in enumerate(candidate_rows, start=1):
        row_id = row.get("record_id") or row.get("id") or row.get("序号") or str(index)
        doi = normalize_key(row.get("doi") or row.get("DOI"))
        log = logs_by_id.get(row_id) or logs_by_doi.get(doi) or {}
        item = dict(row)
        item["id"] = row_id
        for key in ["status", "download_status", "file", "final_path", "reason", "failure_reason"]:
            if log.get(key):
                item[key] = log[key]
        return_status = log.get("download_status") or log.get("status")
        if return_status:
            item["status"] = return_status
        if log.get("final_url") and not item.get("landing_page_url"):
            item["landing_page_url"] = log["final_url"]
        merged.append(item)
    return merged


def read_metrics(run_root: Path, explicit_path: str | None) -> dict[str, dict[str, str]]:
    paths = []
    if explicit_path:
        paths.append(Path(explicit_path))
    paths.append(run_root / "期刊指标.csv")
    paths.append(run_root / "journal_metrics.csv")
    metrics: dict[str, dict[str, str]] = {}
    for path in paths:
        for row in read_csv(path.expanduser()):
            for key_name in ["journal", "source", "venue"]:
                key = normalize_key(row.get(key_name))
                if key:
                    metrics[key] = row
    return metrics


def apply_metrics(rows: list[dict[str, str]], metrics: dict[str, dict[str, str]]) -> None:
    for row in rows:
        metric_row = metrics.get(normalize_key(row.get("journal"))) or {}
        for field in ["impact_factor", "jcr_quartile", "metric_year", "metric_source", "indexing", "venue_note"]:
            if not row.get(field) and metric_row.get(field):
                row[field] = metric_row[field]


def status(row: dict[str, str] | None) -> str:
    if not row:
        return ""
    return row.get("status") or row.get("download_status") or "metadata_only"


def file_name(row: dict[str, str]) -> str:
    value = row.get("file") or row.get("final_path") or ""
    return Path(value).name if value else ""


def reason(row: dict[str, str]) -> str:
    raw = row.get("reason") or row.get("failure_reason") or ""
    return REASON_ZH.get(raw, raw)


def title(row: dict[str, str]) -> str:
    return row.get("title") or row.get("paper") or "未命名文献"


def esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def metric_text(row: dict[str, str]) -> str:
    # A value alone is not a verified metric: both the year and source are
    # needed to make the report's provenance claim meaningful.
    if not row.get("metric_year") or not row.get("metric_source"):
        return "待核验"
    parts = []
    impact_factor = row.get("impact_factor") or row.get("if")
    if impact_factor:
        parts.append(f"IF {impact_factor}")
    if row.get("jcr_quartile"):
        parts.append(row["jcr_quartile"])
    if row.get("indexing"):
        parts.append(row["indexing"])
    if row.get("metric_year") or row.get("metric_source"):
        source = " / ".join(value for value in [row.get("metric_year"), row.get("metric_source")] if value)
        if source:
            parts.append(source)
    return "；".join(parts) if parts else "待核验"


def action_text(row: dict[str, str]) -> str:
    row_status = status(row)
    if row_status == "success_pdf":
        return "先核对题名、DOI 和正文；当前只校验 PDF 结构"
    if row_status in {"success_html", "success_xml", "saved_html_unverified", "saved_xml_unverified"}:
        return "核对是否为正文、登录页或占位页；不计为已获取全文"
    if row_status == "inaccessible":
        return "用学校库/VPN；不行就馆际互借或联系作者"
    if row_status == "metadata_only":
        return "继续查作者主页、机构仓储、Unpaywall"
    if row_status == "broken_link":
        return "用 DOI 重新打开出版社页面"
    return reason(row) or "人工复查"


def status_class(row_status: str) -> str:
    if row_status == "success_pdf":
        return "ok"
    if row_status in {"success_html", "success_xml", "saved_html_unverified", "saved_xml_unverified"}:
        return "warn"
    return "bad"


UNVERIFIED_PAGE_STATUSES = {"success_html", "success_xml", "saved_html_unverified", "saved_xml_unverified"}


def report_counts(rows: list[dict[str, str]]) -> tuple[int, int, int, int]:
    pdf_count = sum(status(row) == "success_pdf" for row in rows)
    page_count = sum(status(row) in UNVERIFIED_PAGE_STATUSES for row in rows)
    pending_count = sum(status(row) not in {"success_pdf", "excluded"} for row in rows)
    metric_count = sum(metric_text(row) != "待核验" for row in rows)
    return pdf_count, page_count, pending_count, metric_count


def report_font() -> str:
    """Use an embedded local CJK TrueType font, with ReportLab's CID fallback."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont

    candidates = [
        Path(__file__).resolve().parent.parent / "fonts" / "NotoSansSC-Regular.ttf",
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/simsun.ttc"),
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ]
    for font_path in candidates:
        if not font_path.exists():
            continue
        try:
            pdfmetrics.registerFont(TTFont("LiteratureReportCJK", str(font_path)))
            return "LiteratureReportCJK"
        except Exception:
            # Some installed TTC/OTF fonts have outlines ReportLab cannot embed.
            continue
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    return "STSong-Light"


def print_pdf(rows: list[dict[str, str]], title_text: str, source_log: Path | None, pdf_path: Path) -> None:
    """Build a readable Chinese PDF locally. No browser or shell is invoked."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle

    font = report_font()
    body_style = ParagraphStyle("body", fontName=font, fontSize=9, leading=13, wordWrap="CJK", alignment=TA_LEFT)
    small_style = ParagraphStyle("small", parent=body_style, fontSize=8, leading=11)
    heading_style = ParagraphStyle("heading", parent=body_style, fontSize=13, leading=18, spaceBefore=14, spaceAfter=8)
    title_style = ParagraphStyle("title", parent=body_style, fontSize=20, leading=27, spaceAfter=10)

    def paragraph(value: object, style: ParagraphStyle = body_style) -> Paragraph:
        return Paragraph(esc(value).replace("\n", "<br/>"), style)

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=pdf_path.parent, suffix=".pdf.part", delete=False) as temporary:
        temporary_path = Path(temporary.name)
    doc = SimpleDocTemplate(str(temporary_path), pagesize=landscape(A4), leftMargin=30, rightMargin=30,
                            topMargin=28, bottomMargin=30, title=title_text, author="literature-downloader")
    pdf_count, page_count, pending_count, metric_count = report_counts(rows)
    story = [paragraph(title_text, title_style), paragraph(f"来源日志：{source_log or '未找到日志'}", small_style), Spacer(1, 8)]
    story.append(paragraph(f"文献总数：{len(rows)}　PDF 结构已校验：{pdf_count}　网页/XML 待核验：{page_count}　待处理或核验：{pending_count}　指标来源已标注：{metric_count}"))
    story.append(Spacer(1, 7))
    story.append(paragraph("校验边界：success_pdf 仅表示文件通过 PDF 结构检查，不保证文件就是目标论文。阅读前仍需核对题名、DOI 和正文；保存的 HTML/XML 以及旧日志中的网页/XML 成功状态均列为待核验，不计为已获取全文。"))
    story.append(Spacer(1, 6))
    story.append(paragraph("期刊指标：缺少年份或来源时显示“待核验”；已有来源也需人工确认其准确性。登录页、付费墙或平台限制可通过学校图书馆、机构 VPN、馆际互借或联系作者继续处理。"))

    def add_section(section_title: str, section_rows: list[dict[str, str]], unresolved: bool) -> None:
        story.append(paragraph(section_title, heading_style))
        if not section_rows:
            story.append(paragraph("无"))
            return
        data = [[paragraph(value, small_style) for value in ["#", "年份", "题名 / DOI", "期刊/来源", "指标/出处", "状态", "文件 / 建议"]]]
        for row in section_rows:
            paper = title(row) + ("\n" + row["doi"] if row.get("doi") else "")
            note = action_text(row) if unresolved else "\n".join(value for value in [file_name(row), action_text(row)] if value)
            values = [row.get("id"), row.get("year"), paper, row.get("journal"), metric_text(row), STATUS_ZH.get(status(row), status(row)), note]
            data.append([paragraph(value, small_style) for value in values])
        widths = [doc.width * fraction for fraction in [0.035, 0.045, 0.28, 0.105, 0.13, 0.14, 0.265]]
        table = LongTable(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eaf0f6")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
            ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(table)

    add_section("PDF 结构已校验（论文身份仍待核验）", [row for row in rows if status(row) == "success_pdf"], False)
    add_section("待处理或核验的文献", [row for row in rows if status(row) != "success_pdf"], True)

    def page_number(canvas, document) -> None:
        canvas.saveState()
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawRightString(document.pagesize[0] - 30, 16, f"第 {document.page} 页")
        canvas.restoreState()

    try:
        doc.build(story, onFirstPage=page_number, onLaterPages=page_number)
        temporary_path.replace(pdf_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def make_html(rows: list[dict[str, str]], title_text: str, source_log: Path | None) -> str:
    total = len(rows)
    pdf_count, page_count, pending_count, metric_count = report_counts(rows)
    cards = [
        ("文献总数", total),
        ("PDF 结构已校验", pdf_count),
        ("网页/XML 待核验", page_count),
        ("待处理或核验", pending_count),
        ("指标来源已标注", metric_count),
    ]
    card_html = "\n".join(
        f"<div class='card'><div class='num'>{number}</div><div class='label'>{label}</div></div>"
        for label, number in cards
    )
    success_rows = [row for row in rows if status(row) == "success_pdf"]
    failed_rows = [row for row in rows if status(row) != "success_pdf"]

    def table(section_rows: list[dict[str, str]], unresolved: bool) -> str:
        body = []
        for row in section_rows:
            row_status = status(row)
            status_text = STATUS_ZH.get(row_status, row_status)
            note = action_text(row) if unresolved else "；".join(value for value in [file_name(row), action_text(row)] if value)
            body.append(
                "<tr>"
                f"<td class='idx'>{esc(row.get('id'))}</td>"
                f"<td>{esc(row.get('year'))}</td>"
                f"<td><div class='paper-title'>{esc(title(row))}</div><div class='doi'>{esc(row.get('doi'))}</div></td>"
                f"<td>{esc(row.get('journal'))}</td>"
                f"<td>{esc(metric_text(row))}</td>"
                f"<td><span class='badge {status_class(row_status)}'>{esc(status_text)}</span></td>"
                f"<td>{esc(note)}</td>"
                "</tr>"
            )
        return "\n".join(body) or "<tr><td colspan='7'>无</td></tr>"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{esc(title_text)}</title>
<style>
body {{ font-family: "Microsoft YaHei", "Noto Sans CJK SC", Arial, sans-serif; margin: 34px; color: #172033; line-height: 1.55; background: #ffffff; }}
h1 {{ font-size: 30px; margin: 0 0 6px; letter-spacing: 0; }}
h2 {{ margin-top: 30px; border-bottom: 2px solid #e5e7eb; padding-bottom: 7px; font-size: 20px; }}
.sub {{ color: #667085; margin-bottom: 20px; }}
.cards {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin: 18px 0 22px; }}
.card {{ border: 1px solid #d7dee8; border-radius: 8px; padding: 13px; background: #f8fafc; }}
.num {{ font-size: 26px; font-weight: 800; color: #0f766e; }}
.label {{ color: #475467; font-size: 13px; }}
.note {{ background: #fff7ed; border: 1px solid #fdba74; padding: 12px 14px; border-radius: 8px; margin: 14px 0; }}
.tip {{ background: #ecfdf5; border: 1px solid #86efac; padding: 12px 14px; border-radius: 8px; margin: 14px 0; }}
table {{ width: 100%; border-collapse: collapse; font-size: 11.5px; table-layout: fixed; }}
th, td {{ border: 1px solid #d7dee8; padding: 7px; vertical-align: top; overflow-wrap: anywhere; }}
th {{ background: #eef2f7; text-align: left; color: #344054; }}
.idx {{ width: 32px; text-align: center; font-weight: 700; }}
.paper-title {{ font-weight: 700; color: #111827; }}
.doi {{ color: #667085; margin-top: 4px; font-size: 10.5px; }}
.badge {{ display: inline-block; border-radius: 999px; padding: 2px 7px; font-size: 11px; font-weight: 700; white-space: nowrap; }}
.ok {{ background: #dcfce7; color: #166534; }}
.warn {{ background: #fef3c7; color: #92400e; }}
.bad {{ background: #fee2e2; color: #991b1b; }}
code {{ background: #f3f4f6; padding: 2px 5px; border-radius: 4px; }}
@media print {{
  body {{ margin: 14mm; }}
  .cards {{ grid-template-columns: repeat(5, 1fr); }}
  table {{ font-size: 9.5px; }}
  h2 {{ break-after: avoid; }}
  tr {{ break-inside: avoid; }}
}}
</style>
</head>
<body>
<h1>{esc(title_text)}</h1>
<div class="sub">来源日志：{esc(source_log) if source_log else "未找到日志"}。本报告只统计合法直连下载结果。</div>
<div class="cards">{card_html}</div>
<div class="tip">校验边界：success_pdf 仅表示文件通过 PDF 结构检查，不保证就是目标论文。请先核对题名、DOI 和正文。保存的 HTML/XML（包括旧日志中的 success_html/success_xml）均待核验，不计为已获取全文。</div>
<div class="tip">影响因子/分区缺少年份或来源时显示“待核验”；已标注来源也需人工确认其准确性。</div>
<div class="note">没下成通常不是程序坏了，而是出版社或数据库限制自动直连下载。遇到 403、登录页、订阅页时，应使用学校图书馆/VPN、馆际互借、机构仓储或联系作者。</div>

<h2>PDF 结构已校验（论文身份仍待核验）</h2>
<table>
<thead><tr><th style="width:32px">#</th><th style="width:42px">年份</th><th>题名 / DOI</th><th style="width:115px">期刊/来源</th><th style="width:105px">影响因子/分区</th><th style="width:70px">状态</th><th>文件/动作</th></tr></thead>
<tbody>{table(success_rows, False)}</tbody>
</table>

<h2>待处理或核验的文献</h2>
<table>
<thead><tr><th style="width:32px">#</th><th style="width:42px">年份</th><th>题名 / DOI</th><th style="width:115px">期刊/来源</th><th style="width:105px">影响因子/分区</th><th style="width:70px">状态</th><th>建议</th></tr></thead>
<tbody>{table(failed_rows, True)}</tbody>
</table>

<h2>给学生的下一步</h2>
<ol>
<li>先核对 PDF 的题名、DOI 和正文，再读其中和课题最贴近的 5-8 篇，边读边做笔记。</li>
<li>影响因子/分区只用于初筛，不要代替论文质量判断；重点看方法、数据、结论和可复现性。</li>
<li>对未下载成功但很重要的文献，用 DOI 走学校图书馆、机构 VPN 或馆际互借。</li>
<li>CSV 日志只用于追溯，不需要普通阅读时打开。</li>
<li>需要打开原文网页时，看 <code>文章地址总表.csv</code>。</li>
</ol>
</body>
</html>
"""


def make_readme(run_root: Path, title_text: str, pdf_path: Path, html_path: Path, rows: list[dict[str, str]], source_log: Path | None = None, pdf_current: bool = False) -> str:
    pdf_count, page_count, pending_count, metric_count = report_counts(rows)
    return "\n".join(
        [
            f"# {title_text}",
            "",
            "先看这个：",
            "",
            f"- PDF 报告：`{pdf_path}`" if pdf_current and pdf_path.exists() else f"- HTML 报告：`{html_path}`",
            f"- 文献目录：`{run_root}`",
            f"- 来源下载日志：`{source_log or choose_log(run_root)[0] or '未找到日志'}`",
            f"- PDF 结构已校验：`{pdf_count}`（论文身份仍待核验）",
            f"- 网页/XML 待核验：`{page_count}`（不计为已获取全文）",
            f"- 待处理或核验：`{pending_count}`",
            f"- 指标来源已标注：`{metric_count}`",
            "",
            "PDF 结构检查不验证论文身份；阅读前请核对题名、DOI 和正文。保存的 HTML/XML 及旧日志网页/XML 成功状态仍需人工核验。指标缺少年份或来源时显示“待核验”。",
            "",
            "说明：没有直接下载成功的文献，多数是出版社或数据库限制自动直连下载。建议用学校图书馆、机构 VPN、馆际互借或联系作者继续获取。",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="生成普通人能看懂的中文文献下载报告。")
    parser.add_argument("--run-root", required=True, help="文献下载任务目录。")
    parser.add_argument("--title", default="文献下载报告")
    parser.add_argument("--metrics-csv", help="可选：包含 journal,impact_factor,jcr_quartile,metric_year,metric_source,indexing 的 CSV。")
    parser.add_argument("--pdf", action="store_true", help="使用 ReportLab 在本地生成中文 PDF，不启动浏览器。")
    args = parser.parse_args()

    run_root = Path(args.run_root).expanduser().resolve()
    source_log, rows = choose_log(run_root)
    if not rows:
        print(f"没有找到可用下载日志：{run_root}", file=sys.stderr)
        return 2
    rows = normalize_rows(merge_candidate_and_log(choose_candidate_rows(run_root), rows))
    apply_metrics(rows, read_metrics(run_root, args.metrics_csv))

    html_path = run_root / "下载报告.html"
    pdf_path = run_root / "下载报告.pdf"
    readme_path = run_root / "README_先看我.md"

    html_path.write_text(make_html(rows, args.title, source_log), encoding="utf-8")
    pdf_error = None
    if args.pdf:
        try:
            print_pdf(rows, args.title, source_log, pdf_path)
        except ImportError as error:
            pdf_error = f"缺少本地 PDF 依赖：{error}。HTML 已保留；请安装 requirements.txt 后重试。"
        except Exception as error:
            pdf_error = f"本地 PDF 生成失败：{error}。HTML 已保留。"
    readme_path.write_text(make_readme(run_root, args.title, pdf_path, html_path, rows, source_log, bool(args.pdf and not pdf_error)), encoding="utf-8")

    if source_log and source_log.resolve() != (run_root / "总下载日志.csv").resolve():
        shutil.copyfile(source_log, run_root / "总下载日志.csv")

    print(f"HTML: {html_path}")
    if args.pdf and not pdf_error:
        print(f"PDF: {pdf_path}")
    elif pdf_error:
        print(f"PDF: {pdf_error}", file=sys.stderr)
    print(f"README: {readme_path}")
    return 1 if pdf_error else 0


if __name__ == "__main__":
    raise SystemExit(main())

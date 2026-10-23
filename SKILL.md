---
name: literature-downloader
description: 整理文献检索计划与候选表，批量保存可直接访问的论文文件，校验 PDF、记录失败并续跑，生成中文清单和报告。用于文献包、已知 DOI 或题名清单的全文获取；正文写作和综述论证沿用 academic-research-suite。
metadata:
  local_version: "2026-10-24.1"
  upstream: "Lucaswangzcx/literature-downloader-skill"
---

# 文献检索与下载

Codex 适配版，来源版本见 `LOCAL_ADAPTATION.json`。浏览器交互默认使用另行配置的 **ego**，文献管理可沿用已有 **Zotero**。不自动启动 Edge、额外浏览器配置、Selenium 或远程调试会话。PDF 报告使用本地 ReportLab，不调用浏览器。这些浏览器和文献管理工具不包含在本仓库中。

## 选择路径

- 有 DOI、题名或文献清单：先核对这些标识符及元数据，不重新做泛主题检索。
- 只有主题：结合学科、时间范围、语言和目标生成检索式，用 ego 检索。`litplan.py` 只生成搜索入口；配置的数据源与数量是计划，不是已实现的数据库采集器。年份需在实际检索页筛选、逐条核对。
- 少量论文可直接用 ego 中已有的 Zotero Connector 保存。需要批量候选表和直链下载时再运行本技能脚本。
- 直链需要登录状态时，使用 ego 正常可见的 PDF 操作；不要导出或重放 Cookie。遇登录或真实可见验证时，按照 ego 技能交还用户处理，再在同一任务空间恢复。

浏览器 API 以当时读取的 ego 技能及 `ego-browser skill` 为准。没有数据库权限时记录获取障碍，不把安装技能说成拥有全文访问权。

## 批量运行

Python 3.10+；依赖见 `requirements.txt`。从技能目录执行，或把脚本替换为绝对路径。

```text
python scripts/prepare_harvest_run.py --topic "研究主题" --keywords "概念一|同义词,概念二" --output-root <输出目录> --run-name <新任务名>
```

初始化只用于新目录，已有目录会停止，避免清空已收集数据。把实际检索并核对过的记录填入 `candidate_table.csv`，使用稳定且唯一的 `record_id`。字段和状态见 `references/batch-harvest.md`。

```text
python scripts/rank_candidates.py --run-root <任务目录>
python scripts/download_accessible_fulltexts.py --run-root <任务目录>
python scripts/make_student_outputs.py --run-root <任务目录>
python scripts/generate_readable_report.py --run-root <任务目录> --title "文献下载报告" --pdf
python scripts/organize_final_outputs.py --run-root <任务目录>
```

关键词分级只是相关性初筛，不代表论文质量、方法可靠性或期刊评价。不会因可下载而提高优先级，也不默认降低综述优先级。

## 校验与续跑

- `success_pdf`：通过实际文件头、完整结尾、可解析页结构等 PDF 结构检查；至少一页，不需要用户提供密码。无密码可读取的权限加密 PDF 可通过。
- 结构有效不证明论文身份、所需内容完整性或结论可靠。用于引用前仍需核对题名、作者、DOI 和具体章节。
- `saved_html_unverified` / `saved_xml_unverified`：网页或 XML 保存到待核验目录，不计入已下载 PDF。旧 `success_html/xml` 也按待核验处理。
- 不把 HTTP 200、文件后缀或网页大小当作下载成功证据。下载先写临时文件，校验后提交；失败响应不覆盖有效 PDF。
- 默认再次运行：重校验已有成功 PDF，保留有效成功记录，处理新记录并重试未成功项。`--retry-failed` 只处理有历史未成功状态的记录，不处理新增未记录项、不重下有效成功文件。
- 原始状态来自 `download_logs/download_log.csv`，中文汇总与内部快照是派生数据。整理目录不移动或删除续跑必需的原始配置、候选表和日志。

下载失败仍保留条目与原因。可经学校图书馆、机构入口、作者主页或仓储继续核查，参见 `references/full-text-access.md`。

## 与 Zotero、ARS 配合

需要写入 Zotero 时使用现有 Zotero 技能，沿用用户指定的来源与目标分类；不直接修改 Zotero 数据库，不自动把下载任务扩大为全库导入。元数据收集、文件结构校验与论文论证分别记录。正文写作、综述比较和引用证据检查沿用 ARS。

## 交付

优先给 HTML 或 PDF 报告、实际 PDF 目录及待处理清单，区分结构有效 PDF、待核验网页/XML和未获取文件。优先级与下载状态分别展示。期刊指标附年份和来源，未核验时注明，不推断数值。

报告与目录约定见 `references/output-reporting.md`。`去重清单.csv` 是记录模板，空表不表示已经自动执行 DOI、题名或文件哈希去重。

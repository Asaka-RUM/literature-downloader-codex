# 批量候选表与续跑

## 原始数据

以下数据保留原位置供续跑读取：`config.json`、`candidate_table.csv`、`download_logs/download_log.csv`、`downloaded_fulltexts/` 和 `unverified_content/`。中文清单与内部目录为派生输出或快照，不能用旧快照覆盖新日志。

`prepare_harvest_run.py` 只创建新任务，不覆盖已存在目录。候选表使用它生成的完整表头，至少保留：

```text
record_id,title,authors,year,journal,doi,source_database,pdf_url_candidate,landing_page_url,candidate_urls_json,priority,exclusion_reason_if_any
```

`record_id` 稳定且唯一；`candidate_urls_json` 是可选 URL 数组。记录可核对来源，不填学校密码、Cookie 或访问令牌。登录保护的下载改用 ego 页面交互。

关键词分级使用 `include_terms`、`secondary_terms`、`exclude_terms`，只是相关性初筛。高相关但下载失败的文献仍保留高优先级；可下载的低相关文献不会自动升为高优先级。

## 状态

| 代码 | 含义 |
|---|---|
| `success_pdf` | PDF 结构有效，论文身份与实际内容仍需核对 |
| `saved_html_unverified` | 已保存 HTML，尚未确认是全文 |
| `saved_xml_unverified` | 已保存 XML，尚未确认是全文 |
| `metadata_only` | 有记录但没有可用文件入口 |
| `inaccessible` | 遇登录、订阅或访问限制 |
| `failed` | 获取或内容校验失败，查看失败原因 |
| `excluded` | 根据排除理由跳过 |

旧 `success_html/xml` 不当作已核验全文，继续放入待处理清单。

## 续跑

再次执行下载器默认重校验已成功 PDF，保留有效文件，处理新记录并重试未成功项。指定 `--retry-failed` 则只重试历史未成功项，不处理新增未记录候选，不重下有效成功文件。

续跑后重新生成中文清单和报告，再整理目录。报告应采用最新原始下载日志，而非上一轮汇总。下载历史另存 `download_logs/download_history.csv`。

## 指标与去重

期刊指标填在候选表或 `journal_metrics.csv`，同时提供 `metric_year` 与 `metric_source`。脚本不联网查指标。

去重清单只是模板；需要去重时按 DOI、规范化题名及文件哈希检查，记录依据，不静默删除版本。

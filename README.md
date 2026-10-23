# Literature Downloader for Codex

面向 Codex 的中文文献检索与下载工作流：整理候选表、批量保存可访问文件、校验 PDF、可靠续跑，并生成中文 HTML / PDF 报告。

这是 [Lucaswangzcx/literature-downloader-skill](https://github.com/Lucaswangzcx/literature-downloader-skill) 的独立适配版，基于上游提交 [`0e8f306`](https://github.com/Lucaswangzcx/literature-downloader-skill/tree/0e8f306fc5205fc90657fbd916a952fa9c9d773a)。保留原作者 MIT 许可证与来源记录；不是上游官方版本。

**English:** A Codex skill for literature collection, structurally validated PDF downloads, resumable logs, relevance-based lists, and Chinese reports. Browser interactions default to ego; report generation never launches a browser.

## 本版改进

| 项目 | 行为 |
|---|---|
| PDF 校验 | 检查实际文件头、EOF、页结构与内容流；拒绝 HTML 伪装、损坏文件和需密码的 PDF |
| 网页与 XML | 保存为待核验内容，不凭后缀、HTTP 200 或页面大小宣称获取全文 |
| 文献优先级 | 保留高、中、低关键词相关性分组，不因可下载而升级，不默认降低综述优先级 |
| 失败重试 | 保留有效成功记录、原文件和历史日志；可仅重试历史未成功项 |
| 整理目录 | 保留续跑必需的配置、候选表和原始日志，不搬走原始数据 |
| 报告 | 使用 ReportLab 本地生成中文 PDF，同时保留 HTML；不调用 Edge 或其他浏览器打印 |
| 初始化 | 已有任务目录会停止，避免重置候选记录与下载日志 |

PDF 结构有效只代表文件能被解析，不证明它就是目标论文、正文齐全或结论可靠。引用前仍需核对题名、作者、DOI 与具体内容。无密码可正常读取的权限加密 PDF 可以通过；单页论文不会仅因页数被拒绝。

## 安装到 Codex

需要 **Python 3.10+**、Git，以及 `requirements.txt` 中的 `pypdf`、`reportlab`。建议使用自己的 Python 虚拟环境，并确保 Codex 执行脚本时使用同一个环境。

Windows PowerShell：

```powershell
git clone https://github.com/Asaka-RUM/literature-downloader-codex.git "$env:USERPROFILE\.agents\skills\literature-downloader"
python -m pip install -r "$env:USERPROFILE\.agents\skills\literature-downloader\requirements.txt"
```

macOS / Linux：

```bash
git clone https://github.com/Asaka-RUM/literature-downloader-codex.git "$HOME/.agents/skills/literature-downloader"
python3 -m pip install -r "$HOME/.agents/skills/literature-downloader/requirements.txt"
```

如果已有同名技能，先备份并合并改动，不要覆盖已有安装。下一次 Codex 消息可使用 `$literature-downloader`；未刷新时重启 Codex。

## 在 Codex 中使用

```text
用 $literature-downloader 检索“温室环境监测与单片机系统设计”相关文献，
先核对题名、作者、年份和来源，整理候选表，
再下载可直接访问的 PDF，记录失败原因，并生成中文报告。
```

浏览器交互默认使用另行配置的 **ego-browser**；少量论文可配合已有 **Zotero Connector** 保存。Zotero、ego 和 ARS 都不包含在本仓库中，也不是运行直链下载脚本的 Python 依赖。

脚本不会自动继承浏览器 Cookie 或机构登录状态。需要登录的下载由用户在正常浏览器流程中完成；本项目不增加学校数据库访问权限，不绕过付费墙或验证码。

## 直接运行脚本

以下命令从仓库根目录执行。准备一个新的任务目录：

```bash
python scripts/prepare_harvest_run.py --topic "温室环境监测" --keywords "greenhouse|温室,sensor|传感器" --output-root literature_runs --run-name greenhouse
```

`litplan.py` 和初始化脚本生成检索计划与入口，不是自动搜索所有数据库的爬虫。先通过实际检索核对文献，并填写 `literature_runs/greenhouse/candidate_table.csv`；使用稳定、唯一的 `record_id`。

```bash
python scripts/rank_candidates.py --run-root literature_runs/greenhouse
python scripts/download_accessible_fulltexts.py --run-root literature_runs/greenhouse
python scripts/make_student_outputs.py --run-root literature_runs/greenhouse
python scripts/generate_readable_report.py --run-root literature_runs/greenhouse --title "文献下载报告" --pdf
python scripts/organize_final_outputs.py --run-root literature_runs/greenhouse
```

再次运行下载器默认重校验成功 PDF，处理新记录，并重试未成功记录。仅重试有历史记录的未成功项：

```bash
python scripts/download_accessible_fulltexts.py --run-root literature_runs/greenhouse --retry-failed
```

此模式不处理新增未记录条目，也不重下校验有效的成功文件。续跑后重新生成清单和报告；不要重新初始化同一个任务目录。

## 输出与校验范围

- `下载报告.html` / `下载报告.pdf`：中文可读报告；未核验指标不当作确定事实。
- `已下载全文/`：结构有效的 PDF，仍需核对论文身份。
- `待核验内容/`：保存的网页或 XML，不计入已下载 PDF。
- `高优先级文献.csv` 等：关键词相关性初筛，与是否下载成功相互独立。
- `待处理文献清单.csv`：获取失败或内容待核验的未排除文献。
- `candidate_table.csv`、`config.json`、`download_logs/`：续跑的原始数据，保留原位置。

去重清单是记录模板，不代表已经自动完成 DOI、题名或文件哈希去重。中文字体会优先使用可用的本地字体；没有适合字体时回退到 ReportLab CID 字体，显示效果可能因 PDF 阅读器而异。

更多说明：[批量表与续跑](references/batch-harvest.md)、[报告规范](references/output-reporting.md)、[来源与改动记录](LOCAL_ADAPTATION.json)。

## 验证与开发

```bash
python -X utf8 -m unittest discover -s tests -v
```

当前包含 **17 项离线回归测试**。本地已验证初始化、分级、HTTP 合成下载、中文清单、PDF 报告、重复整理与失败续跑，并检查中文 PDF 版面。没有以真实出版商或学校登录状态进行端到端验证，不承诺具体平台下载成功率。

测试仅用临时目录、合成 PDF 和模拟请求，不读取 Zotero 文献库或浏览器登录信息。CI 配置运行相同的离线测试。

## 来源与许可证

原始作品版权声明见 [LICENSE](LICENSE)，适配说明见 [NOTICE.md](NOTICE.md)。本项目继续使用 MIT 许可证。更新上游时需保留并合并本地适配，不能假定上游包含这些修改。

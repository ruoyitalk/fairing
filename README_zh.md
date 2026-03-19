# fairing

每日信息聚合工具，用于个人知识管理工作流。从 RSS 订阅源、Newsletter 和 arXiv 论文
拉取内容，输出结构化 Markdown 日报到 Obsidian，并发送邮件摘要。

[ruoyi_talk](https://github.com/ruoyitalk) 项目的组成部分。

**作者：** ruoyi &lt;zhangjunjie@apache.org&gt;
**协议：** [MIT](LICENSE)

---

## 工作流程

每次运行分四个阶段：

1. **采集** — 拉取所有已配置的 RSS 订阅源
2. **去重** — 过滤掉在前几天已写入过的文章
3. **输出** — 生成 Obsidian 日报和 NotebookLM 纯文本（已配置 `NOTEBOOKLM_DIR` 时）；可选生成中文翻译版
4. **通知** — 通过 SMTP 发送 HTML 邮件摘要；内容与上次相同时发出警告并跳过（MD5 校验）

输出路径：
- `OBSIDIAN_DIR/YYYY-WXX/YYYY-MM-DD.md`
- `NOTEBOOKLM_DIR/YYYY-WXX/YYYY-MM-DD.md`（已配置时）

同一天多次运行时，新文章会追加到已有文件末尾，不会覆盖。

---

## 环境要求

- Python 3.11 及以上
- macOS、Linux 或 Windows（PowerShell 5+）

---

## 安装

```bash
git clone git@github.com:ruoyitalk/fairing.git
cd fairing
cp .env.example .env
cp config/sources.local.yaml.example config/sources.local.yaml
```

用编辑器打开 `.env` 填写所需配置。所有字段均为可选——未填的字段对应功能会自动跳过。

---

## 运行

**macOS / Linux**

```bash
bash run.sh       # 进入交互式 shell
```

**Windows**

```powershell
.\run.ps1         # PowerShell（推荐）
run.bat           # cmd 包装器
```

Windows 首次使用 PowerShell 需执行一次：

```powershell
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
```

**非交互模式**（定时任务、脚本调用）：

```bash
bash run.sh run
bash run.sh run --chinese
bash run.sh run --all
```

---

## Shell 命令

```
run                    Obsidian 日报 + NotebookLM（已配置时）+ 邮件
run --md               仅 Obsidian，不生成 NotebookLM
run --no-mail          跳过邮件发送
run --chinese          同时生成中文翻译版（需要 GEMINI_API_KEY）
run --fulltext         通过 Firecrawl 抓取文章全文
run --all              --chinese + --fulltext

config                 以表格形式展示所有订阅源
env                    查看 .env 配置（敏感字段脱敏显示）
env set KEY VALUE      在运行时更新 .env 中的某个字段
log                    查看每日运行记录和邮件发送状态
help                   列出所有命令
exit / \q              退出
```

## 斜杠快捷键

```
\r     run                     \rm    run --md
\rq    run --no-mail           \rc    run --chinese
\rf    run --fulltext          \ra    run --all
\c     config                  \e     env
\l     log                     \h     快捷键列表
\q     退出
```

---

## 配置说明

### 环境变量（.env）

| 变量 | 说明 |
|---|---|
| `SMTP_HOST` | SMTP 服务器地址，如 `smtp.163.com` |
| `SMTP_PORT` | SMTP 端口，默认 `465` |
| `SMTP_USER` | 发件人邮箱 |
| `SMTP_PASSWORD` | SMTP 密码或授权码 |
| `MAIL_TO` | 收件人邮箱 |
| `FIRECRAWL_API_KEY` | 启用博客文章全文抓取。免费额度 500 次/月，注册见 [firecrawl.dev](https://firecrawl.dev) |
| `GEMINI_API_KEY` | 启用 `--chinese` 中文翻译。免费额度见 [aistudio.google.com](https://aistudio.google.com) |
| `OBSIDIAN_DIR` | Obsidian vault 路径，默认 `~/Documents/ruoyinote` |
| `NOTEBOOKLM_DIR` | NotebookLM 纯文本输出路径，留空则禁用 |

### 订阅源配置

**`config/sources.yaml`** — 公共订阅源，可提交到 Git。

```yaml
rss:
  - name: ClickHouse Blog
    url: https://clickhouse.com/rss.xml
    category: Database
    lookback_hours: 24        # 省略则使用默认值 24
    firecrawl_fulltext: true  # 抓取文章全文，默认关闭
```

**`config/sources.local.yaml`** — 私人订阅源，已加入 `.gitignore`。

用于存放不想公开的订阅地址。运行时与 `sources.yaml` 合并。

```yaml
rss:
  - name: McKinsey Newsletter
    url: https://kill-the-newsletter.com/feeds/<id>.xml
    category: Strategy / AI
```

邮件型 Newsletter 没有 RSS 地址，可通过 [Kill the Newsletter](https://kill-the-newsletter.com)
生成专属订阅邮箱，收到邮件后自动转换为 RSS。

**字段说明：**

| 字段 | 默认值 | 说明 |
|---|---|---|
| `name` | 必填 | 显示名称 |
| `url` | 必填 | RSS 或 Atom 订阅地址 |
| `category` | `General` | 用于分组展示 |
| `lookback_hours` | `24` | 抓取时间窗口。每天运行时 24 即可；仅对跳过周末的来源（如 arXiv）设为 `48` |
| `firecrawl_fulltext` | `false` | 抓取文章全文，消耗 Firecrawl 额度 |

---

## 内置订阅源

以下订阅源默认包含在 `config/sources.yaml` 中。

| 来源 | 分类 | 抓取窗口 | 全文 |
|---|---|---|---|
| [ClickHouse Blog](https://clickhouse.com/blog) | Database | 24 h | 是 |
| [Databricks Blog](https://www.databricks.com/blog) | Data Platform | 24 h | 是 |
| [Qdrant Blog](https://qdrant.tech/articles/) | AI / Vector DB | 24 h | — |
| [NVIDIA Developer Blog](https://developer.nvidia.com/blog) | AI / Infrastructure | 24 h | — |
| [Lilian Weng](https://lilianweng.github.io) | AI / ML Research | 24 h | — |
| [Eugene Yan](https://eugeneyan.com) | AI / ML | 24 h | — |
| [Anthropic Engineering](https://www.anthropic.com/engineering) | AI / Engineering | 24 h | — |
| [ByteByteGo](https://blog.bytebytego.com) | Architecture | 24 h | — |
| [arXiv cs.DB/cs.AR/cs.OS](https://arxiv.org) | Research | 48 h | — |
| [Apache Calcite Releases](https://github.com/apache/calcite) | Release | 24 h | — |
| [Trino Releases](https://github.com/trinodb/trino) | Release | 24 h | — |
| [ClickHouse Releases](https://github.com/ClickHouse/ClickHouse) | Release | 24 h | — |
| [HackerNews: query optimizer](https://hnrss.org) | Community | 24 h | — |
| [HackerNews: distributed systems](https://hnrss.org) | Community | 24 h | — |
| [r/dataengineering](https://reddit.com/r/dataengineering) | Community | 24 h | — |
| [CMU Database Group](https://www.youtube.com/@CMUDatabaseGroup) | Research / Video | 24 h | — |
| [FinOps Foundation](https://www.finops.org) | Cloud / FinOps | 24 h | — |
| [benn.substack](https://benn.substack.com) | Data / Strategy | 24 h | — |
| [Software Architecture Weekly](https://softwarearchitectureweekly.substack.com) | Architecture | 24 h | — |
| [The Pragmatic Engineer](https://newsletter.pragmaticengineer.com) | Engineering | 24 h | — |
| [Curious Engineer](https://vivekbansal.substack.com) | Engineering | 24 h | — |

对于无官方 RSS 的来源，使用社区维护的 feed（如 Anthropic Engineering 来自
[Olshansk/rss-feeds](https://github.com/Olshansk/rss-feeds)）。

麦肯锡：同上，订阅邮件 Newsletter 后通过 Kill the Newsletter 转为 RSS。

---

## 状态文件

程序在项目根目录维护两个本地文件，均已加入 `.gitignore`。

**`.seen_urls.json`**

记录所有已处理文章的规范化 URL 和标题，按日期归档。

```json
{
  "2026-03-19": {
    "urls":   ["https://规范化后的url", ...],
    "titles": ["规范化后的标题", ...]
  }
}
```

每次运行应用两层去重：

1. **URL 匹配** — 规范化 URL（剥离追踪参数如 utm_*、去除 trailing slash、统一大小写）。
   防止同一篇文章带不同 UTM 参数被重复处理。
2. **标题匹配** — 规范化标题（小写、去除标点）。
   防止同一内容在不同平台以不同 URL 转载时重复出现。

任何命中上述任一层的文章都会被过滤。同天第二次运行只处理第一次运行之后真正新增的内容。
超过 30 天的记录自动清理。

需要重新处理所有文章时，删除此文件即可。

**`.digest_hash`**

保存上次发送邮件时文章内容的 MD5（url + title + excerpt），以当天日期为范围。
作为轻量级安全网：如果某次运行以完全相同的文章集到达邮件发送步骤（极端情况），
则跳过发送。需要强制重新发送时，删除此文件即可。

---

## 项目结构

```
fairing/
├── config/
│   ├── sources.yaml                 公共订阅源（可提交 Git）
│   ├── sources.local.yaml           私人订阅源（gitignored）
│   └── sources.local.yaml.example
├── fairing/
│   ├── __init__.py                  版本和作者信息
│   ├── config.py                    配置加载与合并
│   ├── rss.py                       RSS 抓取，含单源超时重试
│   ├── mckinsey.py                  基于 Firecrawl 的麦肯锡抓取
│   ├── translator.py                Gemini 中文翻译
│   ├── writer.py                    Obsidian / NotebookLM / 中文输出
│   ├── mailer.py                    SMTP 邮件发送
│   └── state.py                     跨天 URL 去重
├── main.py                          命令行入口（交互式 shell）
├── run.sh                           macOS / Linux 启动脚本
├── run.ps1                          Windows PowerShell 启动脚本
├── run.bat                          Windows cmd 启动脚本
└── requirements.txt
```

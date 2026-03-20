# fairing

> English：[README.md](README.md)

**ruoyi_talk** · v1.0.0 · MIT

fairing 是个人 RSS 日报工具，内置主动学习相关性分类器。每天从配置的订阅源拉取文章，按个人口味评分，写入 Obsidian vault 笔记和可选的 NotebookLM 源文件，并发送邮件摘要。每次运行后只需标注少量文章；积累足够的反馈后，分类器自动训练，此后按预测相关性排列文章。*过滤噪音，直达信号。*

---

## 文档

| 文档 | 说明 |
|------|------|
| [docs/TRAINING.md](docs/TRAINING.md) | ML 流水线：嵌入、逻辑回归、衰减权重 |
| [docs/LABELING.md](docs/LABELING.md) | 三层打标体系：`\rate`、`\rate --ext`、`\lb` |
| [docs/BACKUP.md](docs/BACKUP.md) | 备份与恢复参考手册 |
| [docs/PAYLOAD.md](docs/PAYLOAD.md) | Payload 队列集成：`\ps`、`\sd`、`\pd` |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | 完整运维手册：所有命令与故障排查 |

---

## 快速开始

### 1. 克隆并安装

```bash
git clone https://github.com/JiekerTime/fairing.git
cd fairing
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 配置 `.env`

```bash
# 必填
SMTP_USER=your_address@163.com
SMTP_PASSWORD=your_163_auth_code
MAIL_TO=recipient@example.com
GEMINI_API_KEY=AIzaSy...

# 输出目录
OBSIDIAN_DIR=~/Documents/ObsidianVault/fairing
# NOTEBOOKLM_DIR=~/Documents/NotebookLM

# 多端同步
# DATA_DIR=~/OneDrive/fairing

# run 行为默认值
# RUN_CHINESE=false
# RUN_NO_MAIL=false
# RUN_MD=true
# RUN_NOTEBOOK=true
# MAIL_SPLIT_N=2
```

### 3. 配置订阅源

```bash
cp config/sources.local.yaml.example config/sources.local.yaml
# 在 sources.local.yaml 中添加私有订阅源
```

### 4. 启动

```bash
python main.py
```

进入 Shell 后：`\r` 运行首次日报，`\rate` 完成今日打标任务。

非交互式 / cron 用法：

```bash
python main.py run [--chinese] [--no-mail] [--no-md] [--no-notebook] [--force]
```

---

## Shell 命令速查表

| 快捷键 | 命令 | 参数 | 说明 |
|--------|------|------|------|
| `\r` | `run` | `[--no-md] [--no-notebook] [--no-mail] [--chinese] [--force]` | 完整流水线：RSS → 嵌入 → 评分 → 写文件 → 发邮件 → 备份 |
| `\rate` | `rate` | `[--ext]` | 每日必需打标批次；`--ext` 扩展至所有未标文章 |
| `\lb` | `label_browser` | `[关键词]` | 按关键词搜索并修改历史标注 |
| `\ms` | `model_status` | | 分类器状态、标注统计、信号词 |
| `\rd` | `read` | `[N] [--zh]` | 按编号详读文章；不带 N 则列出所有 |
| `\re` | `resend` | | 重建今日文章列表并强制重发邮件 |
| `\dl` | `remd` | | 重建 Obsidian/NotebookLM 文件，不发邮件 |
| `\t` | `toggle` | `<N>` | 按编号启用或禁用 RSS 订阅源 |
| `\c` | `config` | | 列出订阅源及 7 天文章数 |
| `\e` | `env` | `[set KEY VALUE]` | 查看或修改 `.env` 变量 |
| `\l` | `log` | | 运行历史及每源文章数 |
| `\bk` | `backup` | | 手动触发备份 |
| `\rs` | `restore` | | 从备份恢复（展示差异，确认后执行） |
| `\pd` | `payload_queue` | `[clear]` | 查看或清空 payload 队列 |
| `\ps` | `payload_search` | `<关键词>` | 搜索文章并加入 payload 队列 |
| `\sd` | `send_by_id` | `<article_id>` | 按 ID 将指定文章加入 payload 队列 |
| `\li` | `list_index` | | 查看 title_index.jsonl 最近条目 |
| `\?` `\h` | `shortcuts` | | 显示命令速查表 |
| `\q` | `quit` | | 退出 |

---

## 打标按键

| 按键 | 操作 |
|------|------|
| `+` | 标记为感兴趣 |
| `-` | 标记为不感兴趣 |
| `n` | 跳过（不记录标注） |
| `o` | 在浏览器中打开链接 |
| `r` | 详读（抓取全文，在 `$EDITOR` 中打开） |
| `d` | 加入 payload 队列 |
| `p` | 返回上一篇 |
| `s` | 保存进度并退出 |

---

## 多端同步

将 `DATA_DIR` 设为云同步目录（OneDrive、iCloud Drive、Dropbox）：

```bash
DATA_DIR=~/OneDrive/fairing
```

所有运行时数据文件（包括 `feedback.jsonl`、`scoring_store.jsonl` 和已训练模型）均写入 `DATA_DIR`。两台设备的典型工作流：

1. **设备 A** 运行 `\r` 和 `\rate`，数据同步到云端。
2. **设备 B** 运行 `\dl`，从同步数据重建本地文件，不拉取 RSS，不发邮件。
3. 任意设备均可运行 `\rate`——`feedback.jsonl` 同步后两端共享同一个模型。

`BACKUP_DIR` 是独立的本地路径；如需跨设备备份，可将其设为云同步路径。

---

## 配置参考

### 核心 `.env` 变量

| 变量 | 是否必填 | 默认值 | 说明 |
|------|----------|--------|------|
| `SMTP_USER` | 是 | — | 163 发件地址 |
| `SMTP_PASSWORD` | 是 | — | 163 授权码 |
| `MAIL_TO` | 是 | — | 收件地址 |
| `GEMINI_API_KEY` | 是* | — | Gemini 翻译 Key（仅发英文邮件时可不填） |
| `DATA_DIR` | 否 | 项目根目录 | 所有运行时数据文件 |
| `BACKUP_DIR` | 否 | `~/Documents/fairing/data_bak` | 备份目标目录 |
| `OBSIDIAN_DIR` | 否 | `~/Documents/fairing-vault` | Obsidian vault 输出目录 |
| `NOTEBOOKLM_DIR` | 否 | （空） | NotebookLM 输出目录；不填则禁用 |
| `FIRECRAWL_API_KEY` | 否 | — | Firecrawl 全文抓取，供 `\rd` 使用 |
| `TRANSLATOR` | 否 | `gemini` | 翻译后端：`gemini` / `openai` / `claude` |
| `MAIL_SPLIT_N` | 否 | （关闭） | 将摘要邮件拆分为 N 封 |
| `TOP_N` | 否 | `20` | 邮件中全文展示的文章数 |
| `RUN_MD` | 否 | `true` | 永久禁用 Obsidian 输出：设为 `false` |
| `RUN_NOTEBOOK` | 否 | `true` | 永久禁用 NotebookLM 输出：设为 `false` |
| `RUN_CHINESE` | 否 | `false` | 每次都发中文邮件：设为 `true` |
| `RUN_NO_MAIL` | 否 | `false` | 永不发邮件：设为 `true` |

### sources.yaml 字段说明

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `name` | — | 输出文件和打标界面中的显示名称 |
| `url` | — | RSS/Atom 订阅地址 |
| `category` | `General` | Obsidian 笔记和邮件的分组依据 |
| `firecrawl_fulltext` | `false` | 是否对该源开启 Firecrawl 全文抓取 |

回溯窗口现为动态计算（详见 [OPERATIONS.md](docs/OPERATIONS.md)——动态回溯窗口），不再使用每源 `lookback_hours` 字段。

私有订阅填入 `config/sources.local.yaml`（已 gitignore），参照 `config/sources.local.yaml.example` 创建。

---

## License

MIT © [JiekerTime (若呓)](mailto:zhangjunjie@apache.org)

GitHub: https://github.com/JiekerTime/fairing

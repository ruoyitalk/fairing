> English: [OPERATIONS.md](OPERATIONS.md)

# fairing — 运维参考手册

**版本**: v1.0.0

---

## 日常工作流

```
1. \r                      拉取 RSS、评分、写文件、发邮件、备份、抽取待标样本
2. \rate                   完成今日必需打标批次（3–8 篇）
3. \rate --ext（可选）      从历史候选池继续打标
4. \lb（可选）              搜索并修正历史标注
```

rate-gate 确保同一天内不重复执行 `\r`，除非先完成 `\rate` 或使用 `--force`。

---

## 命令参考

### `\r` — 运行日报

```
\r [--no-md] [--no-notebook] [--no-mail] [--chinese] [--force]
```

完整流水线。执行顺序：

1. `fetch_rss()` — 按动态回溯窗口从所有已启用源拉取文章。
2. `filter_unseen()` — 两层去重：规范化 URL + 规范化标题。
3. 可选：对已配置源进行 Firecrawl 全文抓取。
4. `enrich()` — 计算 `all-MiniLM-L6-v2` 嵌入；缓存至 `scoring_store.jsonl`。
5. `score_articles()` — 按个人模型（已部署时）或启发式评分排序。
6. `write_obsidian()` — 写 Obsidian vault `.md`（除非 `--no-md`）。
7. `write_notebooklm()` — 写 NotebookLM 源文件（除非 `--no-notebook`）。
8. `mark_seen()` — 将 URL 记录至 `seen_urls.json`。
9. `send_digest()` — 发送 HTML 邮件（除非 `--no-mail`）；`--chinese` 翻译邮件正文。
10. `run_backup()` — 将数据文件备份至 `BACKUP_DIR`。
11. `_save_pending()` — 抽取 3–8 篇，写入 `rate_pending.json`。

| 参数 | 说明 |
|------|------|
| `--no-md` | 本次跳过 Obsidian 输出 |
| `--no-notebook` | 本次跳过 NotebookLM 输出 |
| `--no-mail` | 跳过发送邮件 |
| `--chinese` | 邮件正文翻译为中文 |
| `--force` | 即使 `rate_pending` 未完成也强制运行 |

持久化默认值：在 `.env` 中设置 `RUN_MD`、`RUN_NOTEBOOK`、`RUN_CHINESE`、`RUN_NO_MAIL`。

---

### `\rate` — 每日必需打标

```
\rate [--ext]
```

不带 `--ext`：展示 `rate_pending.json` 中今日必需样本。处理完所有文章或按 `s` 退出。完成后设置 `completed=true`。

带 `--ext`：扩展模式。要求 `rate_pending.completed == true`。展示 `title_index.jsonl` 中所有未标注文章（最新优先，无时间限制）。

---

### `\lb` — 标注浏览器

```
\lb [英文关键词]
```

浏览并修改历史标注。不带关键词时显示最近标注的 20 篇。带关键词时按标题过滤（大小写不敏感，AND 逻辑）。`PAGE_SIZE=20`；`[n]ext / [p]rev / [q]uit` 导航。

输入文章编号可修改标注，追加写入 `feedback.jsonl` 并触发 `maybe_auto_train()`。

---

### `\ms` — 模型状态

```
\ms
```

显示：
- 分类器部署状态（已部署 / 未部署）。
- 标注统计：总数、正样本数、负样本数。
- 距 `MIN_TOTAL=80` 的进度。
- 已部署时：上次训练的 balanced_accuracy、TF-IDF 高权重信号词。

---

### `\rd` — 详读文章

```
\rd [N] [--zh]
```

不带 `N`：列出上次 `\r` 的所有文章（按评分排序）。

带 `N`：抓取第 N 篇文章全文（若配置 `FIRECRAWL_API_KEY` 则用 Firecrawl，否则用 `requests`），在 `$EDITOR` 中打开。带 `--zh`：在英文正文下附加中文翻译。

---

### `\re` — 重发邮件

```
\re
```

从 `last_run_articles.json` 重建今日完整文章列表并强制发送邮件（绕过 MD5 重复检查）。适用于邮件未收到或配置更改后需要重发的场景。

---

### `\dl` — 重建本地文件

```
\dl
```

从上次运行数据重建 Obsidian 和 NotebookLM 输出文件，不拉取 RSS 也不发邮件。适用于次要设备从同步数据生成本地文件。

---

### `\t` — 切换订阅源状态

```
\t <N>
```

按编号启用或禁用 RSS 订阅源（编号见 `\c`）。将 `disabled` 列表写入 `sources.local.yaml`。

---

### `\c` — 配置 / 订阅源

```
\c
```

列出所有已配置 RSS 订阅源，包含：
- 编号。
- 名称和 URL。
- 7 天文章数。
- 启用/禁用状态。

---

### `\e` — 环境变量

```
\e
\e set KEY VALUE
```

不带参数：显示所有 `.env` 变量和当前 run 行为默认值。

带 `set KEY VALUE`：在 `.env` 中更新或添加变量，始终显示 `RUN_*` 的当前有效值。

---

### `\l` — 运行日志

```
\l
```

显示运行历史：日期、每源文章数、邮件发送状态，以及遇到的 Feed 错误。

---

### `\bk` — 手动备份

```
\bk
```

手动触发 `run_backup()`，与 `\r` 后的自动备份相同。若今日已有快照则覆盖。

---

### `\rs` — 恢复备份

```
\rs
```

交互式恢复流程：
1. `list_backups()` — 展示可用快照（最新优先）。
2. 选择日期。
3. `all_identical()` — MD5 检查；若与当前文件相同则跳过。
4. `diff_summary()` — 逐文件对比报告。
5. 输入 `yes` 确认。
6. `restore_backup()` — 将文件从备份复制到 `DATA_DIR`。

---

### `\pd` — Payload 队列

```
\pd
\pd clear
```

`\pd`：查看 `payload_queue.json` 当前内容。
`\pd clear`：清空队列（需确认）。

---

### `\ps` — Payload 搜索

```
\ps <英文关键词>
```

按标题搜索所有已知文章，供 payload 入队使用。关键词取 AND 逻辑（大小写不敏感）。分页展示结果；选择条目确认后加入 `payload_queue.json`，可选为所选文章打标。

---

### `\sd` — 按 ID 发送

```
\sd <article_id>
```

按 16 位十六进制 `article_id` 查找文章并加入 `payload_queue.json`。显示元信息供确认。入队后可选打标。

---

### `\li` — 查看文章索引

```
\li
```

显示 `title_index.jsonl` 中最近的条目。适用于为 `\sd` 查找 `article_id`。

---

### `\?` / `\h` — 帮助

```
\?
```

显示快捷键速查表。

---

### `\q` — 退出

```
\q
```

退出交互式 Shell。

---

## 关键概念

### article_id

```python
article_id = sha256(normalize_url(url))[:16]
```

从规范化 URL 派生的 16 位十六进制（64 比特）稳定标识符，在所有数据文件和 payload 队列中通用。

### Rate-Gate（打标门禁）

每次 `\r` 后，`rate_pending.json` 以 `completed=false` 写入。同一天内下次 `\r` 检查此文件，未完成则阻断。使用 `\r --force` 可跳过。

目的：确保重新运行日报前至少完成一次打标。

### 动态回溯窗口

```python
effective_window = max(LOOKBACK_MIN_HOURS, hours_since_last_run)
LOOKBACK_MIN_HOURS = 25
```

fairing 在运行延迟时自动延长回溯窗口，补齐遗漏文章。首次运行以 `2026-03-20` 为起始纪元。

### MAIL_SPLIT_N

在 `.env` 中设置 `MAIL_SPLIT_N` 后，摘要邮件将拆分为 N 封（每封一封），用于规避邮件客户端对超长邮件的渲染限制。

### top_n()

`top_n()` 按评分选取前 N 篇文章作为邮件摘要主体，其余文章仅显示标题。默认 `N=20`，可通过 `.env` 中的 `TOP_N` 配置。

---

## 故障排查

### Rate-gate 阻断

```
Warning: \rate incomplete — run \rate before next \r (or use --force)
```

运行 `\rate` 完成今日打标样本，或使用 `\r --force` 强制跳过。

### `\rate --ext` 阻断

```
Error: mandatory \rate not completed — run \rate first
```

`\rate --ext` 要求 `rate_pending.completed == true`，请先运行 `\rate`。

### 邮件未发送

- 检查 `.env` 中的 `SMTP_USER`、`SMTP_PASSWORD`、`MAIL_TO`。
- 使用 `\re` 重试发送，无需重新运行完整流水线。
- 查看 `\l` 中的错误信息。

### 模型未部署

- 通过 `\ms` 检查标注数量，需满足 `MIN_TOTAL=80`、`MIN_POS=5`、`MIN_NEG=5`。
- 若数量已满足但模型仍未部署，说明 `balanced_accuracy < 0.75`。继续打标，多样化样本可提升准确率。
- 强制重训：从 `DATA_DIR` 删除 `personal_model.pkl` 和 `personal_scaler.pkl`，再运行 `\rate`。

### `\l` 中的 Feed 错误

- 验证订阅 URL 是否仍有效。
- 使用 `\t <N>` 临时禁用故障源。
- 检查 `lookback_hours` 是否合适——部分源（如 arXiv）需要设为 `48`。

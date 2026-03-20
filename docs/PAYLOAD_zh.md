> English: [PAYLOAD.md](PAYLOAD.md)

# fairing — Payload 集成参考手册

**版本**: v1.0.0

---

## 流水线位置

```
fairing（run_digest / \rate / \rate --ext / \sd / \ps）
  └─ payload_queue.json          待推送文章存根

payload（外部消费方）
  └─ 读取 payload_queue.json
  └─ 按 article_id 去重
  └─ 抓取全文内容
  └─ 管理自身状态
```

---

## Article ID

```python
article_id = sha256(normalize_url(url))[:16]
```

- 16 位十六进制 = 64 比特熵。
- 碰撞概率：100 年日常运营约 9×10⁻⁸。
- `normalize_url()` 去除追踪参数并规范化 scheme/host，同一篇文章的不同 URL 形式映射到相同 ID。

---

## payload_queue.json 结构

队列中每个条目为一个 JSON 对象：

```json
{"article_id": "a1b2c3d4e5f6a7b8", "url": "https://...", "title": "...", "source": "HN", "queued_date": "2026-03-21"}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `article_id` | string | `sha256(normalize_url(url))[:16]`，16 位十六进制 |
| `url` | string | 文章 URL（原始，非规范化） |
| `title` | string | 加入队列时的文章标题 |
| `source` | string | RSS 源名称 |
| `queued_date` | string | 加入队列的北京日期（YYYY-MM-DD） |

该文件为 JSON 数组。fairing 追加条目并在写入前按 `article_id` 去重。

---

## 三种添加方式

### 1. 打标时按 `d` 键（`\rate` / `\rate --ext`）

打标卡片界面中按 `d`，将当前文章立即加入 `payload_queue.json`，无需离开卡片。

### 2. `\sd <id>` — 按 ID 发送

```
\sd a1b2c3d4e5f6a7b8
```

在搜索池（见下文）中按 `article_id` 查找文章，展示元信息并提示确认：

```
Title:  Distributed Query Optimizer in CockroachDB
Source: HN
Date:   2026-03-21

Send to payload queue? [y/n]:
```

确认后，可选择为该文章打标：

```
Label this article? [+/-/n]:
```

### 3. `\ps <关键词>` — Payload 搜索（批量）

```
\ps query optimizer
```

在搜索池中按关键词（大小写不敏感，AND 逻辑）搜索文章，分页展示结果。用户选择条目后批量确认：

```
Send 3 articles to payload queue? [y/n]:
```

确认后，可选择为每篇所选文章打标。

---

## 搜索池构建

`\sd` 和 `\ps` 从以下三个来源合并构建搜索池：

| 优先级 | 来源 | 说明 |
|--------|------|------|
| 1（主要） | `title_index.jsonl` | 所有已见文章；最全面 |
| 2（备用） | `scoring_store.jsonl` | 有缓存嵌入的文章；是 title_index 的子集 |
| 3（补充） | `last_run_articles.json` | 最近一次 `\r` 的输出；覆盖最新文章 |

三个来源按 `article_id` 合并去重，`title_index.jsonl` 的标题和元信息优先。

---

## 队列管理（`\pd`）

```
\pd          # 查看当前队列内容
\pd clear    # 清空整个队列
```

`\pd` 以编号列表展示队列，包含 `article_id`、标题、来源和加入日期。

`\pd clear` 在清空 `payload_queue.json` 前会提示确认。

---

## 数据文件写入汇总

| 操作 | 写入文件 |
|------|----------|
| 打标时按 `d` | `payload_queue.json`（追加 + 去重） |
| `\sd <id>` 确认后 | `payload_queue.json`（追加 + 去重） |
| `\ps` 批量确认后 | `payload_queue.json`（追加 + 去重） |
| `\sd` / `\ps` 后可选打标 | `feedback.jsonl`（追加） |
| `\pd clear` 确认后 | `payload_queue.json`（清空） |

---

## Payload 消费方职责

fairing 只向 `payload_queue.json` 写入文章存根，消费方负责：

1. **读取** `payload_queue.json`，提取 `article_id` 和 `url`。
2. **按 `article_id` 去重**，对照自身已处理历史。
3. **抓取全文**（通过 Firecrawl、requests 或自有方式）。
4. **管理自身状态**——fairing 不追踪哪些条目已被消费。
5. **不修改** `payload_queue.json`——队列清空请在 fairing 内使用 `\pd clear`。

---

## 动态回溯窗口

fairing 使用动态回溯窗口，避免因 `\r` 延迟运行而遗漏文章。

```python
effective_window = max(LOOKBACK_MIN_HOURS, hours_since_last_run)
LOOKBACK_MIN_HOURS = 25   # 最小值：始终超过一个自然日
```

首次运行（无 `last_run_time` 记录）时，以 `2026-03-20` 为起始纪元，该日期后发布的文章均可进入候选。

这意味着：
- 正常每日运行：窗口 = 25 小时（覆盖调度抖动）。
- 跳过一天：窗口 = 49+ 小时（自动追赶）。
- Payload 消费方应预期文章来自不固定长度的时间段。

> English version: [PAYLOAD.md](PAYLOAD.md)

# fairing — Payload 集成参考

**版本**：v1.1.0

---

## 架构边界

fairing 和 payload 是两个职责明确分离的服务：

```
┌─────────────────────────────────────────┐
│  fairing                                │
│                                         │
│  RSS 发现 → 摘要抓取（不全时补充）     │
│  → 嵌入 → 评分 → 打标                  │
│  → 邮件摘要 → payload_queue.json        │
└────────────────────┬────────────────────┘
                     │  文章 stub
                     ▼
┌─────────────────────────────────────────┐
│  payload 消费方                         │
│                                         │
│  全文抓取 → 阅读 / 归档                │
│  → 阅读后判断                           │
│  → 写回 feedback.jsonl                  │
└─────────────────────────────────────────┘
```

**fairing 负责**：发现文章、过滤噪音、生成嵌入、训练相关性分类器、发送每日摘要邮件。它回答的是"哪些文章值得读"。

**payload 消费方负责**：抓取全文、呈现给用户深度阅读、决定后续处理方式（归档、标注、送入 LLM 等）。

fairing 不代替用户阅读全文。`\rate` 中的 `o` 键在浏览器打开原文，这是 fairing 阅读支持的全部范围。

---

## Article ID

```python
article_id = sha256(normalize_url(url))[:16]
```

- 16 位十六进制 = 64 bit 熵，单文章碰撞概率可忽略。
- `normalize_url()` 去除追踪参数并归一化 scheme/host，同一文章在不同来源映射到相同 ID。
- payload 消费方必须以 `article_id` 作为主去重键，不能用原始 URL。

---

## payload_queue.json 结构

每条记录是一个 JSON 对象：

```json
{
  "article_id": "a1b2c3d4e5f6a7b8",
  "url":        "https://...",
  "title":      "...",
  "source":     "HN",
  "queued_date": "2026-03-22"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `article_id` | string | `sha256(normalize_url(url))[:16]` |
| `url` | string | 原始文章 URL（未归一化） |
| `title` | string | 入队时的标题 |
| `source` | string | RSS 来源名称 |
| `queued_date` | string | 入队的北京日期（YYYY-MM-DD） |

文件为 JSON 数组，fairing 写入前按 `article_id` 去重。

---

## 四种加入方式

### 1. `\rate` / `\rate --ext` 中按 `d`

打标卡片界面按 `d` 立即入队，可选同时标注为有价值。卡片保持显示，打标流程不中断。

### 2. `\sd <id>` — 按 ID 入队

```
\sd a1b2c3d4e5f6a7b8
```

（`\sd` → `enqueue`）在搜索池中按 `article_id` 查找文章，确认后入队。

### 3. `\ps [关键词]` — 队列搜索

```
\ps                   # 浏览全部文章（翻页）
\ps query optimizer   # 按标题关键词过滤
```

（`\ps` → `queue_search`）分页展示结果，跨页选择文章编号，确认后批量入队。

### 4. `\im <file.csv>` — 批量导入

```
\im ~/Downloads/articles.csv
```

（`\im` → `import_csv`）读取 CSV 文件，逐行处理。支持打标、入队或两者同时操作。

CSV 格式——两列，无需表头：

```csv
article_id,action
5e07b775ab1f3af6,+q
a1b2c3d4e5f6a7b8,-
deadbeef00000001,q
cafebabe12345678,s
```

| action | 含义 |
|--------|------|
| `+` | 标注为有价值 |
| `-` | 标注为不感兴趣 |
| `q` | 仅入队（不打标） |
| `+q` | 标注有价值 **且** 入队 |
| `-q` | 标注不感兴趣 **且** 入队 |
| `s` | 跳过（不做任何操作） |

以 `#` 开头的行视为注释，自动忽略。

---

## 队列管理

```
\pd          # 查看当前队列内容
\pd clear    # 清空队列
```

（`\pd` → `queue`）`\pd clear` 是从 fairing 内部重置队列的唯一支持方式。payload 消费方不应直接修改 `payload_queue.json`。

---

## payload 消费方应做的事

1. **轮询** `payload_queue.json`（按计划或按需）。
2. **去重**：以 `article_id` 对比自身已处理历史。
3. **抓取**全文（Firecrawl、Jina、requests 或任何方式）。
4. **呈现**内容供用户深度阅读。
5. **清空**队列（`\pd clear`）或自行维护已消费 ID 列表。

---

## 搜索池构建

`\sd` 和 `\ps` 的搜索池由三个来源按 `article_id` 合并：

| 优先级 | 来源 | 说明 |
|--------|------|------|
| 1 | `title_index.jsonl` | 所有见过的文章 |
| 2 | `scoring_store.jsonl` | 有嵌入缓存的文章 |
| 3 | `last_run_articles.json` | 最近一次 `\r` 的输出 |

---

## 动态回溯窗口

```python
effective_window = max(25, hours_since_last_run)
```

首次运行以 `2026-03-20` 为起点。跳过的天数会自动补抓。payload 消费方应预期收到来自不固定时间范围的文章。
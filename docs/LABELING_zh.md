> English: [LABELING.md](LABELING.md)

# fairing — 打标系统参考手册

**版本**: v1.0.0

---

## 概述

fairing 采用三层打标体系。三层均写入同一个 `feedback.jsonl`，每次标注后均触发 `maybe_auto_train()`。

```
\r (run_digest)
  └─ _save_pending()          抽样 → rate_pending.json
        │
        ▼
\rate  （第一层 — 每日必需批次）
  └─ _run_mandatory_rate()   完成今日采样；rate-gate 阻断下次 \r
        │
        ▼
\rate --ext  （第二层 — 扩展打标）
  └─ _run_extended_rate()    从 title_index 标注所有未标文章；无时间限制
        │
        ▼
\lb  （第三层 — 标注浏览器）
  └─ _run_label_browser()    搜索并编辑已标注条目

三层均写入 → feedback.jsonl → maybe_auto_train()
```

---

## 数据文件

### rate_pending.json

记录今日必需打标样本的状态。由 `\r` 写入，由 `\rate` 消费。

```json
{
  "run_date":   "2026-03-21",
  "sample_urls": ["https://...", "https://..."],
  "done_urls":   ["https://..."],
  "completed":  false
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `run_date` | string | 创建该样本的 `\r` 运行日期（北京时间） |
| `sample_urls` | list[str] | 本批次 URL 有序列表 |
| `done_urls` | list[str] | 已标注或跳过的 URL |
| `completed` | bool | 全部 sample_urls 处理完毕时为 `true` |

### feedback.jsonl

追加写入的训练标注文件。加载时按 URL 去重，保留最新条目。

```json
{"url": "https://...", "title": "...", "source": "HN", "label": 1, "label_index": 42, "date": "2026-03-21"}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `url` | string | 文章 URL（原始，非规范化） |
| `title` | string | 标注时的文章标题 |
| `source` | string | RSS 源名称 |
| `label` | int | `1` = 感兴趣，`0` = 不感兴趣 |
| `label_index` | int | 单调递增计数器（用于衰减权重计算） |
| `date` | string | 标注日期（北京时间，YYYY-MM-DD） |

### title_index.jsonl

fairing 见过的所有文章索引。作为 `\rate --ext` 和 `\lb` 的候选池。

```json
{"article_id": "a1b2c3d4e5f6a7b8", "url": "https://...", "title": "...", "source": "HN", "date": "2026-03-21"}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `article_id` | string | `sha256(normalize_url(url))[:16]`，16 位十六进制 |
| `url` | string | 文章 URL |
| `title` | string | 文章标题 |
| `source` | string | RSS 源名称 |
| `date` | string | 首次见到的北京日期 |

---

## 第一层 — 每日必需批次（`\rate`）

### 采样：`_sample_articles()`

在每次 `\r` 结束时由 `_save_pending()` 调用。

```python
n    = min(8, max(3, len(articles) // 4))   # 3–8 篇，与当日文章数成比例
pool = [a for a in articles if a["url"] not in already_labeled]
sample = random.sample(pool, min(n, len(pool)))
```

- 对今日新增未标注文章做**简单随机采样**。
- `already_labeled` = `feedback.jsonl` 中所有 URL 的集合。
- 结果写入 `rate_pending.json`，`completed=false`。

### 卡片循环：`_run_mandatory_rate()`

将 `sample_urls` 中的每篇文章逐张展示为卡片，支持以下按键：

| 按键 | 操作 |
|------|------|
| `+` | 标记为感兴趣（label=1），前进 |
| `-` | 标记为不感兴趣（label=0），前进 |
| `n` | 跳过——不记录标注，前进 |
| `o` | 在系统浏览器中打开链接 |
| `r` | 详读：抓取全文，在 `$EDITOR` 中打开 |
| `d` | 加入 payload 队列（`payload_queue.json`） |
| `p` | 返回上一张卡片 |
| `s` | 保存进度并退出 |

每次 `+` 或 `-` 后，`save_feedback()` 追加写入 `feedback.jsonl` 并调用 `maybe_auto_train()`。

### Rate-Gate（打标门禁）

`\r` 结束后，`rate_pending.json` 以 `completed=false` 写入。下次 `\r` 检查：

```
若 rate_pending 存在 且 completed=false 且 run_date=今天：
    阻断运行，提示"请先完成 \rate，或使用 --force 强制执行"
```

使用 `\r --force` 可跳过门禁。该机制确保每日至少完成一次打标。

---

## 第二层 — 扩展打标（`\rate --ext`）

### 前置条件

`\rate --ext` 需同时满足：
1. `rate_pending.json` 存在。
2. `rate_pending.completed == true`（今日必需批次已完成）。

任一条件不满足则阻断，并给出说明。

### 候选池构建

从 `title_index.jsonl` 中取所有不在 `already_labeled` 中的文章，按 `date` 字段倒序排列（最新优先）。

无时间窗口限制——任意日期的文章均可进入候选池。

### 行为

- 与第一层相同的卡片界面。
- 用户按自己节奏打标；按 `s` 退出。
- 不追踪完成状态——多次调用均从当前未标注池继续。

---

## 第三层 — 标注浏览器（`\lb`）

### 调用方式

```
\lb [英文关键词]
```

不带关键词时，显示最近标注的 20 篇文章。带关键词时，按标题过滤。

### 搜索逻辑

- 大小写不敏感，多关键词取 AND 逻辑。
- 搜索池：`feedback.jsonl` 中所有条目（按 URL 去重）。
- 示例：`\lb query optimizer` 匹配标题中同时含 "query" 和 "optimizer" 的文章。

### 分页

`PAGE_SIZE = 20`。每次显示 20 条，`[n]ext / [p]rev / [q]uit` 导航。

### `_edit_label_entry()` 流程

在列表中输入编号可修改标注：

```
Select entry number (or q to quit): 3
Current label: relevant (+1)
New label [+/-/n to skip]: -
```

编辑操作追加新条目到 `feedback.jsonl`（加载时去重，新条目生效）。编辑后调用 `maybe_auto_train()`。

---

## 自动训练触发

任意层保存标注后均调用 `maybe_auto_train()`。

```python
MIN_TOTAL = 80       # 最少总标注数
MIN_POS   = 5        # 最少正样本数
MIN_NEG   = 5        # 最少负样本数
ACCURACY_THRESHOLD = 0.75   # 部署所需最低 balanced_accuracy
```

流程：
1. 加载 `feedback.jsonl`，按 URL 去重（保留最新）。
2. 若 `total < 80` 或 `pos < 5` 或 `neg < 5`：打印进度，返回。
3. 构建嵌入（从 `scoring_store.jsonl` 缓存读取，或重新编码）。
4. 应用衰减权重：`weight = 0.5 ** floor(labels_since / 3)`。
5. 训练 `LogisticRegressionCV` + `StandardScaler`，`class_weight='balanced'`。
6. 使用 `StratifiedKFold` 验证，计算 `balanced_accuracy`。
7. 若 `mean >= 0.75`：保存 `personal_model.pkl` + `personal_scaler.pkl`，模型正式部署。
8. 若 `mean < 0.75`：打印分数，继续收集。

---

## 数据文件写入汇总

| 操作 | 写入文件 |
|------|----------|
| `\r` 完成 | `rate_pending.json`（新样本） |
| 按 `+` 或 `-` 标注 | `feedback.jsonl`（追加） |
| 扩展打标 | `feedback.jsonl`（追加） |
| `\lb` 编辑 | `feedback.jsonl`（追加，加载时去重） |
| 自动训练成功 | `personal_model.pkl`、`personal_scaler.pkl` |
| 打标时按 `d` | `payload_queue.json`（追加） |

---

## 卡片界面按键汇总

| 按键 | 适用场景 | 操作 |
|------|----------|------|
| `+` | `\rate`、`\rate --ext` | 标记感兴趣 |
| `-` | `\rate`、`\rate --ext` | 标记不感兴趣 |
| `n` | `\rate`、`\rate --ext` | 跳过（不记录） |
| `o` | `\rate`、`\rate --ext` | 浏览器打开链接 |
| `r` | `\rate`、`\rate --ext` | 在 `$EDITOR` 中详读 |
| `d` | `\rate`、`\rate --ext` | 加入 payload 队列 |
| `p` | `\rate`、`\rate --ext` | 返回上一篇 |
| `s` | `\rate`、`\rate --ext` | 保存并退出 |
| 数字 | `\lb` | 选择条目编辑 |
| `+`/`-` | `\lb` 编辑提示 | 新标注 |
| `n` | `\lb` 编辑提示 | 取消编辑 |
| `q` | `\lb` | 退出浏览器 |

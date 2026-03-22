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

记录今日打标目标。由 `\r` 写入，由 `\rate` 消费。

```json
{
  "run_date":  "2026-03-21",
  "n":         5,
  "completed": false
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `run_date` | string | `\r` 运行的北京日期 |
| `n` | int | 今日需完成的标注数（3–8，与文章量成比例） |
| `completed` | bool | 今日标注计数达到 `n` 时为 `true` |

`completed` 由任何产生足够标注的路径设置：`\rate`、`\im`、`\lb` 均有效。进度从 `feedback.jsonl` 中实时计算今日标注数，不再追踪具体文章 URL。

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
| `label` | int | `1` = 有价值，`-1` = 不感兴趣 |
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

### 目标数量：`_calc_sample_n()`

在每次 `\r` 结束时调用，确定今日打标目标。

```python
n = min(8, max(3, len(articles) // 4))   # 3–8 篇，与当日文章数成比例
```

写入 `rate_pending.json` 为 `{"run_date": ..., "n": n, "completed": false}`。
若今日 `feedback.jsonl` 中的标注数已达到 `n`，则直接置 `completed=true`。

### 卡片循环：`_run_rate()`

从**全量未标注池**（`title_index.jsonl`，与 `\rate --ext` 相同）随机抽取文章，展示为卡片，直到今日标注数达到 `n`。进度从 `feedback.jsonl` 实时计算，`\im`、`\lb` 等任意路径产生的标注均计入。

| 按键 | 操作 |
|------|------|
| `+` | 标记为有价值（label=1），前进 |
| `-` | 标记为不感兴趣（label=-1），前进 |
| `n` | 跳过——不记录标注，前进 |
| `o` | 在系统浏览器中打开链接 |
| `d` | 加入 payload 队列（`payload_queue.json`） |
| `p` | 返回上一张卡片 |
| `s` | 保存进度并退出 |

每次 `+` 或 `-` 后，`save_feedback()` 追加写入 `feedback.jsonl` 并调用 `maybe_auto_train()`。

### Rate-Gate（打标门禁）

`\r` 结束后写入 `rate_pending.json`。下次 `\r` 检查：

```
若 rate_pending 存在 且 completed=false：
    阻断运行，提示"请先完成 \rate，或使用 --force 强制执行"
```

使用 `\r --force` 可跳过门禁。该机制确保每日至少完成一次打标。任何渠道的今日标注数达到 `n` 后，`completed` 自动置 `true`。

---

## 第二层 — 扩展打标（`\rate --ext`）

### 前置条件

`\rate --ext` 需同时满足：
1. `rate_pending.json` 存在。
2. `rate_pending.completed == true`（今日必需批次已完成）。

任一条件不满足则阻断，并给出说明。

### 候选池构建

从 `title_index.jsonl` 中取所有未标注文章，随机排列（与第一层共用同一套 `_build_unlabeled_pool()` 逻辑）。无时间窗口限制——任意日期的文章均可进入候选池。

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

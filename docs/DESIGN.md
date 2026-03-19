# fairing — Technical Design Document

**版本**: 0.2.0-draft
**依赖 PRD**: docs/PRD.md

---

## 一、整体架构

```
                    ┌─────────────────────────────┐
                    │         每日运行             │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │  1. 采集 (rss.py)            │
                    │     RSS fetch + Firecrawl    │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │  2. 去重 (state.py)          │
                    │     URL规范化 + 标题语义去重   │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │  3. 预处理 (embedder.py)     │
                    │     生成 scoring payload     │
                    │     写入 scoring_store       │
                    └──────┬───────────────┬──────┘
                           │               │
             ┌─────────────▼──┐    ┌──────▼──────────────┐
             │  4a. 归档       │    │  4b. 评分 (scorer.py)│
             │  NotebookLM    │    │     0～1 相关性分数   │
             │  全量，不过滤   │    └──────┬──────────────┘
             └────────────────┘           │
                                  ┌───────▼──────────────┐
                                  │  5. 过滤 + 排序       │
                                  │  score < 0.2 → 丢弃  │
                                  │  Top N → 主 digest   │
                                  │  其余 → 标题附录      │
                                  └───────┬──────────────┘
                                          │
                         ┌────────────────▼────────────────┐
                         │  6. 输出                         │
                         │  Obsidian: Top N + 附录          │
                         │  邮件: Top N + 附录              │
                         └────────────────┬────────────────┘
                                          │
                         ┌────────────────▼────────────────┐
                         │  7. 采样反馈 (\rate)             │
                         │  每日 5~8 篇，多样性采样         │
                         │  +/- 标注 → feedback.jsonl      │
                         └─────────────────────────────────┘
```

---

## 二、新增模块

### 2.1 `fairing/embedder.py`

**职责**：将文章转换为 scoring payload，管理 embedding 缓存。

```python
# 对外接口
def enrich(article: dict) -> dict:
    """
    在 article dict 上追加 scoring 相关字段，并持久化到 scoring_store。
    幂等：同一 URL 不重复计算 embedding。

    追加字段：
      text_for_scoring: str   — 用于 embedding 的清洗文本
      embedding: list[float]  — 384 维向量（sentence-transformers）
    """

def load_store() -> dict[str, dict]:
    """返回 {url: payload} 的全量 scoring_store"""
```

**text_for_scoring 构建规则**：
```
= clean(title)
+ " " + clean(excerpt)[:200]
+ " " + clean(full_text)[:300]   # 仅当 Firecrawl 已抓取时
```

**存储**：`.scoring_store.jsonl`（gitignored）
```jsonl
{"url": "...", "date": "...", "source": "...", "title": "...",
 "text_for_scoring": "...", "embedding": [...]}
```

**embedding 模型**：`sentence-transformers/all-MiniLM-L6-v2`
- 22MB，本地运行
- CPU 推理 ~30ms/篇
- 首次运行自动下载

---

### 2.2 `fairing/scorer.py`

**职责**：对文章打分，支持多种引擎，可通过 `SCORER` 环境变量切换。

```python
class Scorer(Protocol):
    def score(self, article: dict) -> float:
        """返回 0~1 的相关性分数"""

class RuleScorer:        # SCORER=rule（默认）
class ModelScorer:       # SCORER=model（需要 .personal_model.pkl）
class OllamaScorer:      # SCORER=ollama（需要本地 Ollama）
class GeminiScorer:      # SCORER=gemini（需要 GEMINI_API_KEY）

def get_scorer() -> Scorer:
    """根据环境变量返回对应引擎"""
```

**RuleScorer（阶段一，立即可用）**：

基于 `config/persona.yaml` 中的维度定义：
```
score = Σ(dim_weight × dim_score)
dim_score = (title命中数 × 2 + text命中数) / 归一化系数
```
负向词命中 → 扣分（可降至 0）

**ModelScorer（阶段二，30 条标注后启用）**：
```
embedding → LogisticRegression.predict_proba → score
```

---

### 2.3 `fairing/trainer.py`

**职责**：从 `feedback.jsonl` 训练个人分类模型。

**衰减算法**：

衰减单位是"已标注文章数量"，而非日历时间。

```python
DECAY_BASE = 0.5      # 每衰减档，权重减半
DECAY_UNIT = 3        # 每 3 篇新标注 = 1 衰减档

def label_weight(label_index: int, total_labels: int) -> float:
    """
    label_index: 该标注在历史中的位置（0 = 最老）
    total_labels: 当前总标注数
    labels_since = total_labels - 1 - label_index（此标注之后新增的标注数）
    generations = floor(labels_since / DECAY_UNIT)
    weight = DECAY_BASE ** generations
    """
```

示例（总标注 90 条，衰减单位 3）：
```
最新 3 条标注:  weight = 0.5^0  = 1.00
第 4~6 条之前: weight = 0.5^1  = 0.50
第 7~9 条之前: weight = 0.5^2  = 0.25
...
第 88~90 条前: weight = 0.5^29 ≈ 0   （接近零但保留）
```

旧数据永不删除，只是权重趋近零。

**训练流程**：
```python
def train() -> TrainingResult:
    labels  = load_feedback()           # 读 feedback.jsonl
    store   = embedder.load_store()     # 读 scoring_store
    weights = [label_weight(i, len(labels)) for i in range(len(labels))]
    X = [store[l.url].embedding for l in labels]
    y = [l.label for l in labels]       # +1 / -1
    model = LogisticRegression().fit(X, y, sample_weight=weights)
    save(model)                         # → .personal_model.pkl
    return TrainingResult(accuracy=cv_score, n_samples=len(labels))
```

**最低训练门槛**：30 条标注（正负样本均需 ≥ 5 条）。

---

### 2.4 采样反馈（`\rate` 命令）

**采样策略**：从当日文章中取 5～8 篇，保证多样性。

```python
def sample_for_rating(articles, n=7) -> list[dict]:
    """
    分层采样，每层取 1~2 篇：
      高分层  (score > 0.7):   确认模型判断是否正确
      边界层  (0.3~0.7):       最有训练价值的样本
      低分层  (score < 0.3):   校准下边界
      新来源层 (首次出现):      扩展模型见识
    各层比例约 2:3:1:1
    已评分的文章不再出现
    """
```

**交互流程**（shell 内）：
```
fairing > \rate

  今日采样 7/32 篇（来自 5 个来源）

  1/7  [score: 0.81]  ClickHouse Blog
  ─────────────────────────────────────
  ClickHouse 26.2: QBit data type becomes production-ready
  text-index and QBit data type reach GA; improves analytical
  workload performance significantly in benchmark tests...

  [+] 有价值   [-] 不感兴趣   [s] 跳过   [q] 完成
  >
```

**`feedback.jsonl` 格式**：
```jsonl
{
  "url": "https://...",
  "title": "ClickHouse 26.2...",
  "label": 1,
  "score_at_time": 0.81,
  "scorer_type": "rule",
  "label_index": 42,
  "date": "2026-03-20"
}
```

---

### 2.5 输出架构调整

#### NotebookLM（全量归档）

写入时机：评分前，去重后。
```python
# main.py 流水线
articles = fetch_rss(...)
articles = filter_unseen(articles)   # 去重
write_notebooklm(articles, ...)      # ← 全量，此时未过滤
articles = enrich(articles)          # 生成 embedding
articles = score(articles)           # 打分
articles = filter_and_rank(articles) # 过滤 + 排序
write_obsidian(articles, ...)        # 只有 Top N
send_email(articles, ...)            # 只有 Top N
```

#### Obsidian / 邮件（过滤后）

输出格式变化（Obsidian）：
```markdown
# Daily Digest — 2026-03-20
抓取 47 篇 → 归档 47 篇 → 推荐 15 篇 → 附录 8 篇 → 过滤 24 篇

## ★★★★★  ClickHouse 26.2: QBit data type...
...（全量展示，含摘要和图片）

---
### 低优先级（未进入主推荐）
- [0.43] HackerNews: Introduction to LSM trees
- [0.31] Curious Engineer: Weekend project...
```

---

## 三、配置文件

### `config/persona.yaml`（新增）

```yaml
# 阶段一：规则打分用
dimensions:
  disruption:
    weight: 0.30
    boost:   []    # 待用户确认后填写
    penalize: []

  engineering:
    weight: 0.30
    boost:   []
    penalize: [tutorial, beginner, introduction, getting started]

  capability:
    weight: 0.25
    boost:   []
    penalize: [assembly, hardware, 101]

  product:
    weight: 0.15
    boost:   []
    penalize: []

# 过滤阈值
scoring:
  threshold_discard: 0.20    # 低于此分不进入 Obsidian/邮件
  threshold_feature: 0.50    # 高于此分为主推荐
  top_n: 15                  # 主推荐最多篇数
  sample_n: 7                # 每日采样反馈篇数

# 衰减参数
decay:
  base: 0.5      # 每档衰减系数
  unit: 3        # 每 N 篇新标注 = 1 衰减档
```

---

## 四、新增状态文件

| 文件 | 内容 | gitignored |
|------|------|-----------|
| `.scoring_store.jsonl` | 每篇文章的 text_for_scoring + embedding | ✅ |
| `.feedback.jsonl` | 用户标注记录 | ✅ |
| `.personal_model.pkl` | 训练好的分类器 | ✅ |

---

## 五、新增 Shell 命令

| 命令 | 快捷键 | 说明 |
|------|--------|------|
| `rate` | `\rate` | 采样反馈，逐篇展示 |
| `train` | `\train` | 从 feedback 训练模型 |
| `stats` | `\st` | 标注健康度 + 训练建议 |

---

## 六、演进路线

```
Phase 0（当前）:  全量输出，无评分
Phase 1（下一步）: RuleScorer + \rate 采样反馈 + 全量归档拆分
Phase 2（积累后）: ModelScorer（30 条标注后启用）
Phase 3（可选）:  OllamaScorer / 云端 LLM 插件
```

每个 Phase 独立可用，不依赖下一 Phase。

---

## 七、开放问题

| # | 问题 | 状态 |
|---|------|------|
| Q1 | `persona.yaml` 中 boost/penalize 词表由用户填写，还是从标注中自动提取？ | 待定 |
| Q2 | \rate 时是否实时展示当日完整 text_for_scoring，还是只显示 title？ | 待定 |
| Q3 | Obsidian 低优先级附录是否在邮件中也出现？ | 待定（用户倾向不要） |
| Q4 | ModelScorer 门槛 30 条是否合适？ | 待验证 |

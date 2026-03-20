> 英文版：[TRAINING.md](TRAINING.md)

# fairing — 训练原理

**版本**: v1.0.0
**历史版本**: [docs/archive/v0.2.0/](archive/v0.2.0/) *(原始纯中文版本)*

---

## 系统架构

```
Daily Articles / 每日文章
┌─────────────────────────────────────┐
│  title + excerpt + full_text (opt)  │
└──────────────┬──────────────────────┘
               │
               ▼
┌──────────────────────────────────────────┐
│  sentence-transformers/all-MiniLM-L6-v2  │
│  "Distributed query optimizer"           │
│   → [0.12, -0.34, 0.87, ...]  (384-dim) │
└──────────────┬───────────────────────────┘
               │
               ▼
┌──────────────────────────┐
│  StandardScaler          │  zero-mean, unit-variance per dimension
└──────────────┬───────────┘
               │
               ▼
┌──────────────────────────────────────┐
│  LogisticRegressionCV                │
│  class_weight = balanced             │
│  scoring = balanced_accuracy         │
│  MIN_TOTAL = 80 labels required      │
└──────────────┬───────────────────────┘
               │
        P(relevant) ∈ [0, 1]
               │
               ▼
┌──────────────────────────────────────┐
│  Top 20  → full detail display       │
│  Rest    → title-only list           │
└──────────────────────────────────────┘
```

---

## 为什么用 Sentence Transformers？

传统 TF-IDF 把词语视为独立 token。"Query optimization" 和 "query planner" 在 TF-IDF 空间中没有任何重叠，尽管语义高度相关。

Sentence Transformers 将文本编码到稠密向量空间，其中**语义相近的文本在几何上也相近**：

```
Embedding space (2D projection / 二维投影示意)

 ┌─────────────────────────────────────────┐
 │  ● distributed systems                  │
 │   ● consensus algorithm                 │  ← positive cluster / 正样本聚类
 │    ● query optimizer                    │
 │                                         │
 │         · · · · · ·                     │  ← neutral zone / 中性区
 │                                         │
 │             ○ Python beginner           │
 │              ○ tutorial intro           │  ← negative cluster / 负样本聚类
 │               ○ Hello World            │
 └─────────────────────────────────────────┘
```

`all-MiniLM-L6-v2` 是在 10 亿以上句对上训练的 BERT 蒸馏变体。其 384 维嵌入是强先验——**无需微调**。

| 属性 | 值 |
|----------|-------|
| 推理速度 | CPU 上约 30 ms/篇 |
| 语言 | 主要支持英文 |
| 模型大小 | 22 MB，自动缓存 |
| 语义特性 | 余弦相似度 ≈ 语义相似度 |

---

## 为什么用逻辑回归？

有了预训练嵌入，正负类在 384 维空间中往往**线性可分**。线性分类器已经足够，且具有明显优势：

| 属性 | 逻辑回归 | 神经网络 |
|----------|--------------------|----|
| 参数量 | 384 个权重 + 偏置 | 数百万 |
| 过拟合风险（30 样本） | 低（有正则化） | 极高 |
| 训练时间 | 不到 1 秒 | 数分钟 |
| 可解释性 | 高（逐维权重） | 低 |
| 概率校准 | 内置（Platt scaling） | 需要额外步骤 |

---

## 正则化：LogisticRegressionCV

**问题**：384 个特征，约 30 个样本 → 严重过参数化。

L2 正则化在损失中加入惩罚项：

```
loss = cross_entropy(y, ŷ) + (1/C) × ||w||²

C small → strong regularization → weights pushed toward zero
C large → weak regularization  → may overfit
```

`LogisticRegressionCV` 通过交叉验证自动选择 C：

```
C candidates:  0.001  0.01  0.1  1.0  10.0

CV accuracy (example):
  0.001  ████████░░░░  0.62  ← underfit
  0.01   ████████████  0.78
  0.1    ██████████░░  0.76  ← selected
  1.0    ████████░░░░  0.71
  10.0   █████░░░░░░░  0.58  ← overfit
```

---

## 类别平衡

用户倾向于标注感兴趣的文章（正样本），跳过无聊的文章（负样本）→ **类别不平衡**。

不加纠正时，模型会学到"始终预测相关"——原始准确率高，但实际毫无用处。

`class_weight='balanced'` 使用频率倒数权重：

```
Example: 70 positive, 30 negative

w_pos = 100 / (2 × 70) = 0.71
w_neg = 100 / (2 × 30) = 1.67
      ↑ negatives get ~2× gradient signal
```

---

## 评估指标：balanced_accuracy

类别不平衡时，原始准确率会产生误导。我们使用**平衡准确率**：

```
balanced_accuracy = 0.5 × (sensitivity + specificity)
                  = 0.5 × (TP/(TP+FN) + TN/(TN+FP))

Model that always predicts "relevant":
  sensitivity = 1.0  (never misses a positive)
  specificity = 0.0  (misclassifies all negatives)
  balanced_accuracy = 0.5  ← same as random guessing

A well-trained model:
  balanced_accuracy = 0.80  ← actually useful
```

**部署阈值：balanced_accuracy ≥ 0.75**

大致含义：推荐的文章中 75% 确实相关；被过滤掉的文章中 75% 确实不值得阅读。

### 分层 K 折交叉验证

```
n_folds = min(5, min(n_positive, n_negative))

Each fold preserves the positive/negative ratio.
每折保留相同的正负比例。
```

---

## 衰减机制

### 动机

兴趣随时间变化。三周前标记为"高度相关"的话题，掌握基础后可能已变成背景知识。

### 以标注数量衰减（而非日历时间）

遗忘速度与**阅读节奏**挂钩，而非日历时间翻页：
- 每天标注 3 篇 → 衰减更快
- 每周标注 3 篇 → 衰减更慢

```python
DECAY_BASE = 0.5   # weight halves each generation / 每档权重减半
DECAY_UNIT = 3     # every 3 new labels = 1 forgetting generation / 每 3 条新标注 = 1 衰减档

weight = DECAY_BASE ** floor(labels_since / DECAY_UNIT)
```

```
Decay curve / 衰减曲线:

weight │
1.0    │████ ████ ████
       │         ←3→
0.5    │              ████ ████ ████
       │                        ←3→
0.25   │                             ████ ████ ████
0.12   │                                          ████ ███
0.06   │                                               ████
0.0    └──────────────────────────────────────────────────→
       labels added since this label / 此标注之后新增标注数
        0    3    6    9   12   15   18   21   24   27   30
```

旧标注**永不删除**，权重趋近于零但不消失。回到旧话题时，几个新正样本即可超过衰减后的旧证据。

---

## 完整训练循环

```
\rate labeling session / 打标会话
     │
     ├─ show article (text_for_scoring: title + excerpt + full_text snippet)
     │
     ├─ user input: + (relevant) / - (irrelevant) / n (skip) / p (prev) / s (save-quit)
     │
     └─ save_feedback(url, label, label_index, date)
              │
              ▼
       data/feedback.jsonl  ← git-tracked; survives machine changes
       (URL-deduplicated on read: keep latest entry per URL)
              │
              ▼
       maybe_auto_train()
              │
              ├─ pos < 5 OR neg < 5 OR total < MIN_TOTAL (80)?
              │         └─ "keep collecting" → exit
              │
              ▼
       build X (embeddings), y (labels), w (decay weights)
              │
              ▼
       StandardScaler.fit_transform(X)
              │
              ▼
       LogisticRegressionCV
         - inner CV auto-selects C
         - class_weight=balanced
              │
              ▼
       outer StratifiedKFold validation
         → balanced_accuracy per fold → mean ± std
              │
              ├─ mean < 0.75? → "not deployed, keep labeling"
              │
              └─ mean ≥ 0.75? → save model + scaler
                                  → next run uses personal scoring
```

---

## \rate 采样策略

每日必需批次使用**简单随机采样**，从今日新增的未标注文章中抽取：

```python
n    = min(8, max(3, len(articles) // 4))   # 3–8 篇，与当日文章数成比例
pool = [a for a in articles if a["url"] not in already_labeled]
sample = random.sample(pool, min(n, len(pool)))
```

`\rate --ext`（扩展模式）按时间倒序（最新优先）展示 `title_index.jsonl` 中**所有**未标注文章，没有时间窗口限制——用户按自己节奏打标，按 `s` 退出。

---

## 存储设计

| 文件 | 位置 | 是否 Git 追踪 | 备注 |
|------|----------|------------|-------|
| 反馈标注 | `DATA_DIR/data/feedback.jsonl` | **否**（自 v1.0.0） | 重新训练所需的唯一文件 |
| 文章索引 | `DATA_DIR/title_index.jsonl` | 否 | 所有已见文章；`\rate --ext` 和 `\lb` 的候选池 |
| 嵌入缓存 | `DATA_DIR/scoring_store.jsonl` | 否 | 可从文章重建 |
| 分类器 | `DATA_DIR/personal_model.pkl` | 否 | 可从反馈重建 |
| 缩放器 | `DATA_DIR/personal_scaler.pkl` | 否 | 可从反馈重建 |

所有数据文件统一存放在 `DATA_DIR` 下。模型文件丢失时，运行 `\rate` 后，标注数量足够即自动重新训练。

---

## 已知局限

**小样本方差高**
标注 20–50 条时，CV 准确率方差为 ±10–15%。
报告 `0.78 ± 0.12` 意味着真实泛化准确率可能在 0.66–0.90 之间。

**英文专用流水线**
`all-MiniLM-L6-v2` 以英文为主。CJK 字符超过 25% 的文章在摄入时被排除。
`--chinese` 仅将英文文章翻译用于邮件输出，不接受中文内容作为输入。

**突变兴趣漂移**
话题突然转变需要 15–20 条新标注才能让模型适应。衰减机制擅长处理渐变漂移；突变需要集中打标。

**摘要质量**
许多 RSS 源只提供 2–3 句摘要。为对应源开启 Firecrawl 全文抓取可显著改善信号质量。

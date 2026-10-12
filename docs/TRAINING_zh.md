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
│  LogisticRegression + 交叉验证门槛     │
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

## 正则化：LogisticRegression

**问题**：384 个特征，约 30 个样本 → 严重过参数化。

L2 正则化在损失中加入惩罚项：

```
loss = cross_entropy(y, ŷ) + (1/C) × ||w||²

C small → strong regularization → weights pushed toward zero
C large → weak regularization  → may overfit
```

`GridSearchCV` 在每轮训练折内选择 C，缩放器也只在训练折内拟合：

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

平衡准确率是正例召回率和负例召回率的平均值，不等于推荐文章的准确比例。

### 分层 K 折交叉验证

```
n_folds = min(5, min(n_human_positive, n_human_negative))

只用人工标注做留出验证。助手推断的标注可以进入训练折，但不进入测试折；
用三种随机划分复验结果是否稳定。
```

---

## 标注权重

样本仍少时，人工标注保持相同权重。过去“每新增三条就减半”的做法会让一次
标注会话前的大部分证据近乎失效。兴趣变化应通过用户改标和独立验证体现，
不靠自动衰减猜测。

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
              ├─ 有向量的标注少于 80，或人工标注少于 30
              │  （其中正负各少于 10）？
              │         └─ "keep collecting" → exit
              │
              ▼
       build X (embeddings), y (labels)
              │
              ▼
       重复进行人工标注留出验证
         - 缩放器只在每轮训练折拟合
         - 内层交叉验证选 C，class_weight=balanced
         - 助手标注只作训练数据
         → 每轮 balanced_accuracy → mean ± std
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

所有数据文件统一存放在 `DATA_DIR` 下。网页新增标注会在下一次日任务中评估；
CLI 标注也会触发评估。只有人工留出验证过关才会保存模型。

---

## 已知局限

**小样本方差高**
标注 20–50 条时，CV 准确率方差为 ±10–15%。
报告 `0.78 ± 0.12` 意味着真实泛化准确率可能在 0.66–0.90 之间。

**英文专用流水线**
`all-MiniLM-L6-v2` 以英文为主。CJK 字符超过 25% 的文章在摄入时被排除。
`--chinese` 仅将英文文章翻译用于邮件输出，不接受中文内容作为输入。

**突变兴趣漂移**
旧标注可能不再符合当前偏好。应由用户改标并增加新的人工标注；助手推断的标注不能单独验证偏好变化。

**摘要质量**
许多 RSS 源只提供 2–3 句摘要。为对应源开启 Firecrawl 全文抓取可显著改善信号质量。

> English: [BACKUP.md](BACKUP.md)

# fairing — 备份与恢复参考手册

**版本**: v1.0.0

---

## 概述

```
\r (run_digest)
  └─ run_backup()             每次成功运行后自动备份

\bk（手动）
  └─ run_backup()             同一函数，用户手动触发

\rs（恢复）
  └─ list_backups()           列出可用快照
  └─ all_identical()          MD5 检查 — 备份与当前文件相同时跳过
  └─ diff_summary()           逐文件对比
  └─ 输入 "yes" 确认
  └─ restore_backup()         将文件复制回 DATA_DIR
```

---

## 备份文件清单

每次备份复制以下 6 个文件：

| 文件 | 备份中的位置 | 说明 |
|------|------------|------|
| `feedback.jsonl` | `BACKUP_DIR/YYYY-MM-DD/data/feedback.jsonl` | 训练标注——最重要 |
| `seen_urls.json` | `BACKUP_DIR/YYYY-MM-DD/seen_urls.json` | URL 去重状态 |
| `scoring_store.jsonl` | `BACKUP_DIR/YYYY-MM-DD/scoring_store.jsonl` | 嵌入缓存 |
| `title_index.jsonl` | `BACKUP_DIR/YYYY-MM-DD/title_index.jsonl` | 文章索引 |
| `rate_pending.json` | `BACKUP_DIR/YYYY-MM-DD/rate_pending.json` | 打标进度 |
| `payload_queue.json` | `BACKUP_DIR/YYYY-MM-DD/payload_queue.json` | 待推送 payload 文章 |

### 不备份的文件

以下文件有意排除：

| 文件 | 原因 |
|------|------|
| `last_run_articles.json` | 临时文件——下次 `\r` 会重新生成 |
| `digest_hash` | 临时文件——邮件防重发标记，非持久状态 |
| `last_run_time` | 临时文件——运行时时间戳 |
| `personal_model.pkl` | 可从 `feedback.jsonl` 重新训练生成 |
| `personal_scaler.pkl` | 可从 `feedback.jsonl` 重新训练生成 |

---

## 备份目录结构

```
BACKUP_DIR/
  2026-03-19/
    data/
      feedback.jsonl
    seen_urls.json
    scoring_store.jsonl
    title_index.jsonl
    rate_pending.json
    payload_queue.json
  2026-03-20/
    data/
      feedback.jsonl
    ...
  2026-03-21/
    ...
```

- 每个自然日（北京时间）一个目录。
- 同日多次备份会覆盖当天快照。
- `_prune()` 删除超过 `RETAIN_DAYS`（默认 7）天的目录。

---

## 备份写入流程：`run_backup()`

```python
dest = BACKUP_DIR / today_beijing()   # 例如 "2026-03-21"
dest.mkdir(parents=True, exist_ok=True)

for src_path, rel_path in BACKUP_FILES:
    if src_path.exists():
        target = dest / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, target)

_prune(BACKUP_DIR, RETAIN_DAYS)
```

`shutil.copy2` 保留文件元数据。同日重复运行直接覆盖，不报错。`_prune()` 遍历 `BACKUP_DIR`，解析目录名为日期，删除超过 `RETAIN_DAYS` 天的目录。

---

## 恢复流程

`\rs` 执行以下五个步骤：

### 第一步：`list_backups()`

列出 `BACKUP_DIR` 中所有日期目录，最新优先，并显示文件数：

```
Available backups:
  [1] 2026-03-21  (6 files)
  [2] 2026-03-20  (6 files)
  [3] 2026-03-19  (5 files)
Select backup to restore (or q to quit):
```

### 第二步：`all_identical()` — MD5 检查

计算所选备份与对应当前文件的 MD5。若所有文件字节完全一致，则跳过恢复，提示："Backup is identical to current data — nothing to restore."

### 第三步：`diff_summary()` — 逐文件对比

对每个被备份的文件报告状态：

| 状态 | 含义 |
|------|------|
| `identical` | 与当前版本完全一致 |
| `modified` | 存在差异（显示行数变化） |
| `missing in backup` | 快照中不存在此文件 |
| `missing locally` | 当前文件已被删除 |

示例输出：
```
feedback.jsonl     modified  (+12 lines in backup vs live)
seen_urls.json     identical
scoring_store.jsonl modified (+3 entries)
title_index.jsonl  identical
rate_pending.json  modified
payload_queue.json identical
```

### 第四步：确认

```
Restore from 2026-03-20? This will overwrite live files. Type "yes" to confirm:
```

输入非 `yes` 的任何内容均取消操作。

### 第五步：`restore_backup()`

将所选备份目录中的每个文件复制到 `DATA_DIR`，保留子目录结构。

---

## 恢复后的影响

| 恢复的文件 | 影响 |
|-----------|------|
| `feedback.jsonl` | 训练标注回滚；下次 `\rate` 可能以旧数据重新训练 |
| `seen_urls.json` | 备份到当前之间的文章可能在下次 `\r` 中重新出现 |
| `scoring_store.jsonl` | 嵌入缓存回滚；新文章嵌入将重新计算 |
| `title_index.jsonl` | 文章索引回滚；`\rate --ext` 和 `\lb` 的候选池缩小 |
| `rate_pending.json` | 打标进度回滚至备份时状态 |
| `payload_queue.json` | Payload 队列回滚；备份后加入的文章丢失 |

---

## 配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `BACKUP_DIR` | `~/Documents/fairing/data_bak` | 所有备份快照的根目录 |
| `RETAIN_DAYS` | `7` | 保留的每日快照数量，超出则自动清理 |

两者均可在 `.env` 中覆盖。

---

## 多端同步说明

使用云盘同步 `DATA_DIR` 时：

- 备份在执行 `\r` 的设备上自动运行。
- `BACKUP_DIR` 是独立的本地路径——默认**不**跨设备同步。
- 如需跨设备备份覆盖，可将 `BACKUP_DIR` 设为云同步路径（如 OneDrive 子目录）。
- `\rs` 始终恢复到 `DATA_DIR`，若 `DATA_DIR` 通过云盘共享，两端设备均可受益于同一次恢复操作。

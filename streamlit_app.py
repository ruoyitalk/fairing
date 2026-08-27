"""ruoyi_talk · fairing — Streamlit web UI for the RSS digest + payload system."""
import hashlib
import json
import random
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import streamlit as st

# ── path setup ─────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from fairing.paths import (
    rate_pending_file,
    title_index_file,
    payload_queue_file,
    feedback_file,
    model_file,
    last_run_time_file,
    feed_errors_file,
)
from fairing.state import normalize_url

_TZ_BJT = timezone(timedelta(hours=8))

# ── page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="ruoyi_talk",
    page_icon="📰",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── cached file readers (TTL 30s — fast enough for interactive use) ────────────
@st.cache_data(ttl=30)
def _read_title_index() -> list[dict]:
    path = title_index_file()
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


@st.cache_data(ttl=10)
def _read_feedback() -> list[dict]:
    path = feedback_file()
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def _load_json(path: Path, default=None):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    # Invalidate cache so next read picks up new data
    _read_feedback.clear()


def _labeled_urls(feedback: list[dict]) -> set[str]:
    """URL set of labeled articles (last label wins per URL)."""
    seen: dict[str, str] = {}
    for r in feedback:
        url = r.get("url", "")
        if url:
            seen[url] = r.get("label", "")
    return set(seen.keys())


def _dedup_recent(feedback: list[dict], n: int = 15) -> list[dict]:
    """Return the n most-recent labels, deduplicated by URL (keep last occurrence)."""
    seen_urls: set[str] = set()
    deduped = []
    for r in reversed(feedback):
        url = r.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            deduped.append(r)
        if len(deduped) >= n:
            break
    return deduped


def _format_time(ts_str: str) -> str:
    try:
        dt = datetime.fromisoformat(ts_str)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts_str[:16] if ts_str else "—"


def _article_id(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode()).hexdigest()[:16]


def _build_label_pool() -> list[dict]:
    """Build unlabeled article pool matching \\rate logic: shuffle, cap at rate_pending.n."""
    feedback = _read_feedback()
    labeled = _labeled_urls(feedback)
    title_idx = _read_title_index()
    pool = [a for a in title_idx if a.get("url") not in labeled]
    random.shuffle(pool)
    pending = _load_json(rate_pending_file(), {})
    n = pending.get("n", 20)
    if n and n > 0:
        pool = pool[:n]
    return pool


# ── sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("ruoyi_talk")
    st.caption("fairing · RSS 智能摘要")
    st.divider()

    feedback_sb = _read_feedback()
    title_idx_sb = _read_title_index()
    labeled_sb = _labeled_urls(feedback_sb)
    queue_sb = _load_json(payload_queue_file(), [])

    st.metric("已标记文章", len(labeled_sb))
    st.metric("标题索引", len(title_idx_sb))
    st.metric("载荷队列", len(queue_sb) if isinstance(queue_sb, list) else 0)

    lrf = last_run_time_file()
    last_run = lrf.read_text(encoding="utf-8").strip() if lrf.exists() else ""
    st.caption(f"上次运行：{_format_time(last_run) if last_run else '从未'}")

    st.divider()
    page = st.radio(
        "导航",
        ["🏠 仪表板", "🏷️ 文章标记", "📦 载荷队列", "▶️ 运行控制"],
        label_visibility="collapsed",
    )

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
if page == "🏠 仪表板":
    st.header("🏠 仪表板")

    feedback = _read_feedback()
    labeled = _labeled_urls(feedback)
    title_idx = _read_title_index()
    pending = _load_json(rate_pending_file(), {})

    today_str = datetime.now(_TZ_BJT).date().isoformat()
    today_labels = sum(1 for r in feedback if r.get("date", "") == today_str)
    has_model = model_file().exists()
    unlabeled_count = sum(1 for a in title_idx if a.get("url") not in labeled)

    # ── metrics ───────────────────────────────────────────────────────────────
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("今日标记", today_labels)
    with col2:
        rate_n = pending.get("n", 0)
        rate_done = pending.get("completed", False)
        st.metric("今日目标", rate_n, delta="✅ 完成" if rate_done else "⏳ 待完成")
    with col3:
        st.metric("待标记", unlabeled_count)
    with col4:
        if has_model:
            st.metric("模型", "✅ 已训练")
        elif len(labeled) >= 80:
            st.metric("模型", "⏳ 训练中")
        else:
            st.metric("模型", "⏸️ 数据不足")

    st.divider()

    # ── model detail ──────────────────────────────────────────────────────────
    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("📊 模型信息")
        if has_model:
            mtime = datetime.fromtimestamp(model_file().stat().st_mtime)
            st.info(f"最后训练：{mtime.strftime('%Y-%m-%d %H:%M')}")
            pos = sum(1 for r in feedback if r.get("label") == "+")
            neg = sum(1 for r in feedback if r.get("label") == "-")
            st.write(f"样本数：**{len(labeled)}**（正例 {pos} / 负例 {neg}）")
            if len(labeled) > 0:
                st.progress(pos / len(labeled), text=f"正例比例 {pos/len(labeled):.0%}")
        else:
            prog = min(len(labeled) / 80, 1.0)
            if len(labeled) < 80:
                st.warning(f"尚未训练完成，还需 **{80 - len(labeled)}** 条标记（当前 {len(labeled)} / 80）")
            else:
                st.info(f"已有 {len(labeled)} 条标记，模型将在下次运行时自动训练")
            st.progress(prog)

    with col_b:
        st.subheader("⚠️ Feed 状态")
        errors = _load_json(feed_errors_file(), {})
        if errors:
            for src, info in list(errors.items())[:5]:
                consecutive = info.get("consecutive", 0) if isinstance(info, dict) else 0
                st.warning(f"**{src}**: 连续失败 {consecutive} 次")
        else:
            st.success("所有 Feed 正常")

    st.divider()

    # ── recent labels (deduped) ───────────────────────────────────────────────
    st.subheader("🕐 最近标记记录")
    recent = _dedup_recent(feedback, 12)
    if recent:
        rows = [
            {
                "标签": "✅" if r.get("label") == "+" else "❌",
                "标题": r.get("title", "")[:65],
                "来源": r.get("source", ""),
                "日期": r.get("date", "")[:10],
            }
            for r in recent
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("暂无标记记录")

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: LABEL ARTICLES  (auto-loads on visit, no mode selection)
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🏷️ 文章标记":
    st.header("🏷️ 文章标记")

    # Init session state
    if "label_idx" not in st.session_state:
        st.session_state.label_idx = 0
    if "label_pool" not in st.session_state:
        st.session_state.label_pool = []
    if "label_count" not in st.session_state:
        st.session_state.label_count = 0

    # Auto-load on first visit to this page
    if not st.session_state.label_pool:
        st.session_state.label_pool = _build_label_pool()
        st.session_state.label_idx = 0
        st.session_state.label_count = 0

    pool = st.session_state.label_pool
    idx = st.session_state.label_idx

    # Refresh button (top right)
    col_hdr, col_refresh = st.columns([5, 1])
    with col_refresh:
        if st.button("🔄 刷新"):
            st.session_state.label_pool = _build_label_pool()
            st.session_state.label_idx = 0
            st.session_state.label_count = 0
            _read_feedback.clear()
            _read_title_index.clear()
            st.rerun()

    if not pool:
        st.success("🎉 没有待标记的文章！所有文章均已标记。")
    elif idx >= len(pool):
        st.success(f"🎉 本轮标记完成！共标记 {st.session_state.label_count} 篇。")
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("▶️ 继续标记（加载新一批）", type="primary"):
                st.session_state.label_pool = _build_label_pool()
                st.session_state.label_idx = 0
                st.session_state.label_count = 0
                _read_feedback.clear()
                st.rerun()
    else:
        article = pool[idx]
        st.progress(idx / len(pool), text=f"{idx + 1} / {len(pool)}  （本轮已标记 {st.session_state.label_count} 篇）")

        # Article card
        with st.container(border=True):
            st.subheader(article.get("title", "(无标题)"))
            col_m1, col_m2 = st.columns(2)
            with col_m1:
                st.caption(f"📡 {article.get('source', '未知来源')}")
            with col_m2:
                pub = article.get("published", article.get("date", ""))
                st.caption(f"📅 {pub[:10] if pub else '—'}")

            url = article.get("url", "")
            if url:
                display = url if len(url) <= 80 else url[:77] + "..."
                st.markdown(f"🔗 [{display}]({url})")

            summary = article.get("summary", "")
            if summary:
                st.divider()
                st.markdown(summary[:500] + ("…" if len(summary) > 500 else ""))

        # Label buttons
        st.markdown("**这篇文章对你有价值吗？**")
        col1, col2, col3, col4 = st.columns([2, 2, 1, 1])

        def _do_label(label: str, enqueue: bool = False):
            art_url = article.get("url", "")
            record = {
                "url": art_url,
                "title": article.get("title", ""),
                "source": article.get("source", ""),
                "label": label,
                "date": datetime.now(_TZ_BJT).date().isoformat(),
            }
            _append_jsonl(feedback_file(), record)

            if enqueue:
                q = _load_json(payload_queue_file(), [])
                if isinstance(q, list):
                    aid = _article_id(art_url)
                    if not any(x.get("id") == aid for x in q):
                        q.append({
                            "id": aid, "url": art_url,
                            "title": article.get("title", ""),
                            "source": article.get("source", ""),
                        })
                        payload_queue_file().write_text(
                            json.dumps(q, ensure_ascii=False, indent=2)
                        )

            st.session_state.label_idx += 1
            st.session_state.label_count += 1

        with col1:
            if st.button("✅ 有价值", use_container_width=True, type="primary"):
                _do_label("+")
                st.rerun()
        with col2:
            if st.button("❌ 无价值", use_container_width=True):
                _do_label("-")
                st.rerun()
        with col3:
            if st.button("⏭️ 跳过"):
                st.session_state.label_idx += 1
                st.rerun()
        with col4:
            if st.button("📦 入队"):
                _do_label("+", enqueue=True)
                st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: PAYLOAD QUEUE
# ══════════════════════════════════════════════════════════════════════════════
elif page == "📦 载荷队列":
    st.header("📦 载荷队列")

    queue = _load_json(payload_queue_file(), [])
    if not isinstance(queue, list):
        queue = []

    col_m, col_c = st.columns([3, 1])
    with col_m:
        st.metric("队列文章数", len(queue))
    with col_c:
        if queue and st.button("🗑️ 清空", type="secondary"):
            payload_queue_file().write_text("[]")
            st.rerun()

    if queue:
        st.divider()
        for i, item in enumerate(queue):
            with st.container(border=True):
                col_t, col_del = st.columns([5, 1])
                with col_t:
                    st.markdown(f"**{item.get('title', '无标题')}**")
                    st.caption(f"{item.get('source','—')} · `{item.get('id','')}`")
                    url = item.get("url", "")
                    if url:
                        disp = url if len(url) <= 72 else url[:69] + "..."
                        st.markdown(f"[{disp}]({url})")
                with col_del:
                    if st.button("✕", key=f"del_{i}"):
                        queue.pop(i)
                        payload_queue_file().write_text(
                            json.dumps(queue, ensure_ascii=False, indent=2)
                        )
                        st.rerun()
    else:
        st.info("队列为空。在文章标记页面点击「入队」添加文章。")

    st.divider()
    st.subheader("🔍 搜索文章加入队列")
    search_q = st.text_input("关键词搜索标题")
    if search_q:
        kws = search_q.lower().split()
        results = [
            a for a in _read_title_index()
            if all(k in a.get("title", "").lower() for k in kws)
        ][:20]
        if results:
            for a in results:
                col_r, col_add = st.columns([5, 1])
                with col_r:
                    st.write(f"**{a.get('title','')[:70]}** — {a.get('source','')}")
                with col_add:
                    url = a.get("url", "")
                    aid = _article_id(url)
                    if any(x.get("id") == aid for x in queue):
                        st.caption("已在")
                    elif st.button("＋", key=f"add_{aid}"):
                        queue.append({"id": aid, "url": url,
                                      "title": a.get("title",""),
                                      "source": a.get("source","")})
                        payload_queue_file().write_text(
                            json.dumps(queue, ensure_ascii=False, indent=2)
                        )
                        st.rerun()
        else:
            st.info("无匹配结果")

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: RUN CONTROL
# ══════════════════════════════════════════════════════════════════════════════
elif page == "▶️ 运行控制":
    st.header("▶️ 运行控制")

    col_opts, col_run = st.columns([2, 1])
    with col_opts:
        st.subheader("运行选项")
        opt_nomail  = st.checkbox("🔕 不发邮件 (--no-mail)")
        opt_chinese = st.checkbox("🇨🇳 中文翻译 (--chinese)")
        opt_force   = st.checkbox("⚡ 强制执行 (--force)", value=True)
    with col_run:
        st.subheader("执行")
        run_btn = st.button("▶️ 运行摘要", type="primary", use_container_width=True)

    if run_btn:
        cmd = [sys.executable, str(ROOT / "main.py"), "run"]
        if opt_nomail:  cmd.append("--no-mail")
        if opt_chinese: cmd.append("--chinese")
        if opt_force:   cmd.append("--force")

        st.info(f"运行：`{' '.join(cmd)}`")
        output_box = st.empty()
        lines: list[str] = []
        try:
            proc = subprocess.Popen(
                cmd, cwd=str(ROOT),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            for line in proc.stdout:
                lines.append(line.rstrip())
                output_box.code("\n".join(lines[-40:]), language="")
            proc.wait()
            if proc.returncode == 0:
                st.success("✅ 运行完成")
                _read_feedback.clear()
            else:
                st.error(f"❌ 运行失败（退出码 {proc.returncode}）")
        except Exception as e:
            st.error(f"启动失败：{e}")

    st.divider()
    st.subheader("📋 当前配置")
    env_file = ROOT / ".env"
    if env_file.exists():
        lines_cfg = []
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key = line.split("=")[0]
                if any(k in key.upper() for k in ["PASSWORD","KEY","TOKEN","SECRET","SMTP_PASS"]):
                    lines_cfg.append(f"{key}=***")
                else:
                    lines_cfg.append(line)
        st.code("\n".join(lines_cfg))

    st.divider()
    st.subheader("⏰ 定时任务")
    st.info("每天 22:00 自动运行（--force，不受标记门槛限制）")

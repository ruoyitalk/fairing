"""ruoyi_talk · fairing — Streamlit web UI for the RSS digest + payload system."""
import json
import os
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
    model_file,
    last_run_time_file,
    feed_errors_file,
)
from fairing.export import (
    add_to_payload_queue,
    article_id_for,
    load_payload_queue,
    remove_from_payload_queue,
    write_payload_queue,
)
from fairing.trainer import load_feedback
from fairing.web_actions import active_feed_errors, record_feedback
from fairing.web_auth import (
    google_user_allowed,
    homeserver_key_matches,
    parse_allowed_emails,
    read_secret,
    user_email,
)
from fairing.web_articles import is_web_url, latest_articles

_TZ_BJT = timezone(timedelta(hours=8))

# ── page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Fairing · 资讯",
    page_icon="📰",
    layout="wide",
    initial_sidebar_state="expanded",
)


# Authentication terminates in Fairing. Google and the Homeserver recovery key
# share the gate without copying secrets into this repository.
_SECRET_DIR = Path("/run/homeserver-secrets")
try:
    _allowed_emails = parse_allowed_emails(
        read_secret(_SECRET_DIR / "google_allowed_emails")
    )
    _homeserver_key = read_secret(_SECRET_DIR / "context_password")
except (OSError, ValueError):
    st.error("认证配置不可用，请检查服务器密钥挂载。")
    st.stop()

_local_authenticated = bool(st.session_state.get("fairing_homeserver_authenticated"))
_google_authenticated = bool(
    getattr(st.user, "is_logged_in", False)
    and google_user_allowed(st.user, _allowed_emails)
)

if not (_local_authenticated or _google_authenticated):
    st.title("Fairing 资讯")
    st.caption("登录后查看订阅和摘要")

    if getattr(st.user, "is_logged_in", False):
        email = user_email(st.user)
        st.error(f"Google 账号 {email or '未知账号'} 不在允许列表中。")
        if st.button("退出 Google 账号"):
            st.logout()
    else:
        google_col, key_col = st.columns(2)
        with google_col:
            st.subheader("Google 登录")
            st.write("使用允许列表中的 Google 账号。")
            if st.button("使用 Google 登录", type="primary", use_container_width=True):
                st.login("google")
        with key_col:
            st.subheader("Homeserver 密钥")
            submitted_key = st.text_input("密钥", type="password")
            if st.button("使用密钥登录", use_container_width=True):
                if homeserver_key_matches(submitted_key, _homeserver_key):
                    st.session_state["fairing_homeserver_authenticated"] = True
                    st.rerun()
                else:
                    st.error("密钥不正确。")
    st.stop()

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
    return load_feedback()


def _load_json(path: Path, default=None):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


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
    st.title("Fairing")
    st.caption("资讯与日摘")
    st.divider()

    feedback_sb = _read_feedback()
    title_idx_sb = _read_title_index()
    labeled_sb = _labeled_urls(feedback_sb)
    queue_sb = load_payload_queue()

    st.metric("已标记文章", len(labeled_sb))
    st.metric("标题索引", len(title_idx_sb))
    st.metric("载荷队列", len(queue_sb) if isinstance(queue_sb, list) else 0)

    lrf = last_run_time_file()
    last_run = lrf.read_text(encoding="utf-8").strip() if lrf.exists() else ""
    st.caption(f"上次运行：{_format_time(last_run) if last_run else '从未'}")

    st.divider()
    if _google_authenticated:
        st.caption(f"Google：{user_email(st.user)}")
        if st.button("退出登录", use_container_width=True):
            st.logout()
    else:
        st.caption("Homeserver 密钥登录")
        if st.button("退出登录", use_container_width=True):
            st.session_state.pop("fairing_homeserver_authenticated", None)
            st.rerun()

    st.divider()
    page = st.radio(
        "导航",
        ["最新资讯", "运行状态", "文章标记", "载荷队列", "运行控制"],
        label_visibility="collapsed",
    )

if page == "最新资讯":
    st.header("最新资讯")
    st.caption("按收录时间排列，打开原文后再决定是否标记。")
    all_articles = _read_title_index()
    sources = sorted({str(item.get("source")) for item in all_articles if item.get("source")})
    search_col, source_col = st.columns([2, 1])
    with search_col:
        query = st.text_input("搜索标题或来源")
    with source_col:
        selected_source = st.selectbox("来源", ["全部来源", *sources])
    articles = latest_articles(
        all_articles, query=query,
        source="" if selected_source == "全部来源" else selected_source,
    )
    if not all_articles:
        st.info("还没有收录文章。运行一次摘要后再来查看。")
    elif not articles:
        st.info("没有找到符合条件的文章。")
    else:
        st.caption(f"显示最近 {len(articles)} 篇")
        for article in articles:
            with st.container(border=True):
                st.subheader(article.get("title") or "无标题")
                st.caption(f"{article.get('source') or '未知来源'} · {article.get('date') or '日期未知'}")
                st.link_button("阅读原文", article["url"])

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
elif page == "运行状态":
    st.header("运行状态")

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
        st.metric("今日标记目标", rate_n, delta="已完成" if rate_done else "未完成")
    with col3:
        st.metric("待标记", unlabeled_count)
    with col4:
        if has_model:
            st.metric("模型", "已训练")
        elif len(labeled) >= 80:
            st.metric("模型", "等待下次运行")
        else:
            st.metric("模型", "样本不足")

    st.divider()

    # ── model detail ──────────────────────────────────────────────────────────
    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("筛选模型")
        if has_model:
            mtime = datetime.fromtimestamp(model_file().stat().st_mtime)
            st.info(f"最后训练：{mtime.strftime('%Y-%m-%d %H:%M')}")
            pos = sum(1 for r in feedback if r.get("label") == 1)
            neg = sum(1 for r in feedback if r.get("label") == -1)
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
        st.subheader("订阅源错误")
        errors = _load_json(feed_errors_file(), {})
        active_errors = active_feed_errors(errors) if isinstance(errors, dict) else []
        if active_errors:
            for src, consecutive in active_errors[:5]:
                st.warning(f"**{src}**: 连续失败 {consecutive} 次")
        else:
            st.info("最近未记录连续失败的订阅源。")

    st.divider()

    # ── recent labels (deduped) ───────────────────────────────────────────────
    st.subheader("最近标记")
    recent = _dedup_recent(feedback, 12)
    if recent:
        rows = [
            {
                "标签": "有价值" if r.get("label") == 1 else "无价值",
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
elif page == "文章标记":
    st.header("文章标记")

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
        if st.button("刷新"):
            st.session_state.label_pool = _build_label_pool()
            st.session_state.label_idx = 0
            st.session_state.label_count = 0
            _read_feedback.clear()
            _read_title_index.clear()
            st.rerun()

    if not pool:
        st.info("当前没有待标记文章。")
    elif idx >= len(pool):
        st.success(f"本轮已标记 {st.session_state.label_count} 篇。")
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("加载下一批", type="primary"):
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
                st.caption(article.get('source', '未知来源'))
            with col_m2:
                pub = article.get("published", article.get("date", ""))
                st.caption(pub[:10] if pub else '—')

            url = article.get("url", "")
            if is_web_url(url):
                st.link_button("阅读原文", url)

            summary = article.get("summary", "")
            if summary:
                st.divider()
                st.markdown(summary[:500] + ("…" if len(summary) > 500 else ""))

        # Label buttons
        st.markdown("**这篇文章对你有价值吗？**")
        col1, col2, col3, col4 = st.columns([2, 2, 1, 1])

        def _do_label(label: int, enqueue: bool = False):
            record_feedback(article, label)
            _read_feedback.clear()
            if enqueue:
                add_to_payload_queue(article)

            st.session_state.label_idx += 1
            st.session_state.label_count += 1

        with col1:
            if st.button("有价值", use_container_width=True, type="primary"):
                _do_label(1)
                st.rerun()
        with col2:
            if st.button("无价值", use_container_width=True):
                _do_label(-1)
                st.rerun()
        with col3:
            if st.button("跳过"):
                st.session_state.label_idx += 1
                st.rerun()
        with col4:
            if st.button("加入队列"):
                _do_label(1, enqueue=True)
                st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: PAYLOAD QUEUE
# ══════════════════════════════════════════════════════════════════════════════
elif page == "载荷队列":
    st.header("载荷队列")

    queue = load_payload_queue()

    col_m, col_c = st.columns([3, 1])
    with col_m:
        st.metric("队列文章数", len(queue))
    with col_c:
        if queue and st.button("清空队列", type="secondary"):
            write_payload_queue([])
            st.rerun()

    if queue:
        st.divider()
        for i, item in enumerate(queue):
            with st.container(border=True):
                col_t, col_del = st.columns([5, 1])
                with col_t:
                    st.markdown(f"**{item.get('title', '无标题')}**")
                    st.caption(f"{item.get('source','—')} · `{item.get('article_id','')}`")
                    url = item.get("url", "")
                    if is_web_url(url):
                        st.link_button("阅读原文", url)
                with col_del:
                    if st.button("✕", key=f"del_{i}"):
                        remove_from_payload_queue(item.get("article_id", ""))
                        st.rerun()
    else:
        st.info("队列为空。在文章标记页面点击「入队」添加文章。")

    st.divider()
    st.subheader("搜索文章并入队")
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
                    aid = article_id_for(url)
                    if any(x.get("article_id") == aid for x in queue):
                        st.caption("已在")
                    elif st.button("＋", key=f"add_{aid}"):
                        add_to_payload_queue(a)
                        st.rerun()
        else:
            st.info("无匹配结果")

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: RUN CONTROL
# ══════════════════════════════════════════════════════════════════════════════
elif page == "运行控制":
    st.header("运行控制")

    col_opts, col_run = st.columns([2, 1])
    with col_opts:
        st.subheader("运行选项")
        opt_nomail  = st.checkbox("本次不发邮件", value=True)
        opt_chinese = st.checkbox(
            "本次生成中文摘要",
            value=os.environ.get("RUN_CHINESE", "").strip().lower() in ("1", "true", "yes"),
        )
    with col_run:
        st.subheader("执行")
        run_btn = st.button("运行摘要", type="primary", use_container_width=True)

    if run_btn:
        cmd = [sys.executable, str(ROOT / "main.py"), "run"]
        cmd.append("--no-mail" if opt_nomail else "--mail")
        cmd.append("--chinese" if opt_chinese else "--no-chinese")

        st.info("正在生成摘要，请等待运行结束。")
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
                st.success("运行完成")
                _read_feedback.clear()
            else:
                st.error(f"运行失败（退出码 {proc.returncode}）")
        except Exception as e:
            st.error(f"启动失败：{e}")

    st.divider()
    st.subheader("定时任务")
    cron_file = Path(os.environ.get("DATA_DIR", "/data/fairing")) / ".cron_time"
    if cron_file.is_file():
        language = "英文与中文" if os.environ.get("RUN_CHINESE", "").strip().lower() in ("1", "true", "yes") else "英文"
        st.info(f"每日 {cron_file.read_text(encoding='utf-8').strip()} 自动生成{language}摘要，不发送邮件。")
    else:
        st.info("尚未设置定时运行。")

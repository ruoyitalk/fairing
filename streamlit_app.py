"""ruoyi_talk · fairing — Streamlit web UI for the RSS digest + payload system."""
import json
import os
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
from fairing.trainer import load_feedback, model_status
from fairing.config import Config
from fairing.editorial import feedback_by_url, is_visible
from fairing.web_actions import active_feed_errors, record_feedback
from fairing.web_auth import (
    google_user_allowed,
    homeserver_key_matches,
    parse_allowed_emails,
    read_secret,
    user_email,
)
from fairing.web_articles import is_web_url, latest_articles
from fairing.web_digest import latest_digest, plain_markdown
from fairing.writer import select_digest_articles

_TZ_BJT = timezone(timedelta(hours=8))

# ── page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Fairing · 资讯",
    page_icon="📰",
    layout="centered",
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
    st.title("Fairing")
    st.caption("日摘、文章和稍后阅读。")

    if getattr(st.user, "is_logged_in", False):
        email = user_email(st.user)
        st.error(f"Google 账号 {email or '未知账号'} 不在允许列表中。")
        if st.button("退出 Google 账号"):
            st.logout()
    else:
        if st.button("使用 Google 登录", type="primary", use_container_width=True):
            st.login("google")
        with st.expander("使用 Homeserver 密钥"):
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


@st.cache_data(ttl=30)
def _read_latest_digest():
    return latest_digest(Path(os.environ.get("NEWS_DIR", "/data/news")))


@st.cache_data(ttl=60)
def _digest_source_tiers() -> dict[str, int]:
    return {source.name: source.digest_tier for source in Config().rss_sources
            if source.enabled}


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
    """Offer current digest articles first, then recent indexed articles."""
    feedback = _read_feedback()
    labeled = _labeled_urls(feedback)
    pending = _load_json(rate_pending_file(), {})
    n = max(1, min(int(pending.get("n") or 20), 100))
    pool: list[dict] = []
    seen = set(labeled)
    digest = _read_latest_digest()
    labels = feedback_by_url(feedback)
    today = datetime.now(_TZ_BJT).date()
    tiers = _digest_source_tiers()
    digest_articles = select_digest_articles(
        digest[3], limit=n * 2, source_tiers=tiers, labels=labels, as_of=today,
    ) if digest else []
    recent_articles = select_digest_articles(
        _read_title_index(), limit=n * 3, per_source=3,
        source_tiers=tiers, labels=labels, as_of=today,
    )
    for article in [*digest_articles, *recent_articles]:
        url = article.get("url", "")
        if url and url not in seen:
            pool.append(article)
            seen.add(url)
        if len(pool) >= n:
            break
    return pool


def _article_preview(article: dict, *, date_field: str, labels: dict[str, dict],
                     scope: str, excerpt_length: int = 0) -> None:
    """Show one article with direct feedback, without rendering feed Markdown."""
    with st.container(border=True):
        st.markdown(f"**{plain_markdown(article.get('title') or '无标题')}**")
        source = article.get("source") or "未知来源"
        published = article.get(date_field) or "日期未知"
        st.caption(plain_markdown(f"{source} · {published}"))
        summary = str(article.get("summary") or "")
        if excerpt_length and summary:
            excerpt = summary[:excerpt_length]
            st.markdown(plain_markdown(excerpt) + ("…" if len(summary) > excerpt_length else ""))
        url = article["url"]
        if is_web_url(url):
            st.link_button("阅读原文", url)
        previous = labels.get(url, {})
        label = previous.get("label")
        if label == 1:
            st.caption("已标记：有价值")
        elif label == -1:
            st.caption("已标记：广告" if previous.get("reason") == "advertisement"
                       else "已标记：无价值")
        key = f"{scope}_{article_id_for(url)}"
        useful, irrelevant, advertisement = st.columns(3)
        with useful:
            if st.button("有价值", key=f"useful_{key}", disabled=label == 1,
                         use_container_width=True):
                record_feedback(article, 1)
                _read_feedback.clear()
                st.rerun()
        with irrelevant:
            if st.button("无价值", key=f"irrelevant_{key}",
                         disabled=label == -1 and previous.get("reason") != "advertisement",
                         use_container_width=True):
                record_feedback(article, -1)
                _read_feedback.clear()
                st.rerun()
        with advertisement:
            if st.button("广告", key=f"ad_{key}",
                         disabled=label == -1 and previous.get("reason") == "advertisement",
                         use_container_width=True):
                record_feedback(article, -1, reason="advertisement")
                _read_feedback.clear()
                st.rerun()


# ── sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("Fairing")
    st.caption("资讯与日摘")
    st.divider()

    feedback_sb = _read_feedback()
    title_idx_sb = _read_title_index()
    labeled_sb = _labeled_urls(feedback_sb)
    queue_sb = load_payload_queue()

    st.metric("已标记", len(labeled_sb))
    st.metric("收录文章", len(title_idx_sb))
    st.metric("待保存", len(queue_sb) if isinstance(queue_sb, list) else 0)

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
        ["最新资讯", "运行状态", "标记文章", "待保存", "运行控制"],
        label_visibility="collapsed",
    )

if page == "最新资讯":
    st.title("资讯")
    labels = feedback_by_url(_read_feedback())
    view = st.radio("阅读范围", ["本期日摘", "文章库"], horizontal=True,
                    label_visibility="collapsed")
    if view == "本期日摘":
        digest = _read_latest_digest()
        if digest:
            digest_path, digest_day, digest_chinese, digest_articles = digest
            visible = [a for a in digest_articles if is_visible(a, labels)]
            featured = select_digest_articles(
                visible, source_tiers=_digest_source_tiers(), labels=labels,
                as_of=datetime.now(_TZ_BJT).date(),
            )
            st.caption(f"{digest_day} · {'中文' if digest_chinese else '英文'} · 本期 {len(featured)} 篇")
            digest_age = (datetime.now(_TZ_BJT).date() - datetime.fromisoformat(digest_day).date()).days
            if digest_age > 1:
                st.warning(f"这期日摘是 {digest_age} 天前的，可能没有包含最近的新消息。")
            errors = _load_json(feed_errors_file(), {})
            active_errors = active_feed_errors(errors) if isinstance(errors, dict) else []
            if active_errors:
                st.caption(f"有 {len(active_errors)} 个订阅源近期抓取失败，本期可能不完整。详情见「运行状态」。")
            for article in featured:
                _article_preview(article, date_field="published", labels=labels,
                                 scope="digest", excerpt_length=180)
            if not featured:
                st.info("本期没有符合当前筛选的文章；可在文章库显示已过滤内容。")
            st.download_button(
                "下载原始日摘", digest_path.read_bytes(), file_name=digest_path.name,
                mime="text/markdown",
            )
        else:
            st.info("还没有日摘。定时任务运行后会显示在这里。")
    else:
        all_articles = _read_title_index()
        sources = sorted({str(item.get("source")) for item in all_articles if item.get("source")})
        st.caption("按标题或来源查找已收录的文章。")
        query = st.text_input("搜索标题或来源")
        selected_source = st.selectbox("来源", ["全部来源", *sources])
        show_filtered = st.checkbox("显示已过滤的内容", value=False)
        candidates = all_articles if show_filtered else [
            article for article in all_articles if is_visible(article, labels)
        ]
        articles = latest_articles(
            candidates, query=query,
            source="" if selected_source == "全部来源" else selected_source,
        )
        if not all_articles:
            st.info("还没有收录文章。定时任务运行后再来查看。")
        elif not articles:
            st.info("没有找到符合条件的文章。")
        else:
            st.caption(f"显示 {len(articles)} 篇，最多 30 篇。")
            for article in articles:
                _article_preview(article, date_field="date", labels=labels, scope="library")

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
elif page == "运行状态":
    st.header("运行状态")

    feedback = _read_feedback()
    labeled = _labeled_urls(feedback)
    title_idx = _read_title_index()
    today_str = datetime.now(_TZ_BJT).date().isoformat()
    today_labels = sum(1 for r in feedback if r.get("date", "") == today_str)
    training = model_status()
    last_evaluation = training.get("last_evaluation")
    has_model = training["deployed"]
    unlabeled_count = sum(1 for a in title_idx if a.get("url") not in labeled)

    # ── metrics ───────────────────────────────────────────────────────────────
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("今日标记", today_labels)
    with col2:
        st.metric("待标记", unlabeled_count)
    with col3:
        if has_model:
            st.metric("个性化筛选", "已启用")
        elif last_evaluation:
            st.metric("个性化筛选", "评估未达标")
        elif len(labeled) >= 80:
            st.metric("个性化筛选", "待评估")
        else:
            st.metric("个性化筛选", "尚未启用")

    st.caption("标记会立即影响单篇显示和同源排序；模型通过人工标注验证后才参与排序。")

    st.divider()

    # ── model detail ──────────────────────────────────────────────────────────
    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("个性化筛选")
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
            if last_evaluation:
                st.info(
                    f"最近验证：{last_evaluation['cv_accuracy']:.0%}，"
                    f"人工标注 {last_evaluation.get('n_human') or '—'} 条。"
                    "上线门槛为 75%；当前继续使用来源和单篇反馈排序。"
                )
            elif len(labeled) < 80:
                st.info(f"目前有 {len(labeled)} 条标记；达到 80 条后会评估模型。")
            else:
                st.info(f"已有 {len(labeled)} 条标记；下次日任务会评估模型。")
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

    st.subheader("日摘来源")
    cutoff = (datetime.now(_TZ_BJT).date() - timedelta(days=6)).isoformat()
    recent_by_source: dict[str, int] = {}
    latest_by_source: dict[str, str] = {}
    seen_recent: set[str] = set()
    for article in title_idx:
        source = str(article.get("source") or "")
        day = str(article.get("date") or "")[:10]
        url = str(article.get("url") or "")
        if not source or not day:
            continue
        latest_by_source[source] = max(day, latest_by_source.get(source, ""))
        if day >= cutoff and url and url not in seen_recent:
            recent_by_source[source] = recent_by_source.get(source, 0) + 1
            seen_recent.add(url)
    tier_name = {1: "候选", 2: "常规", 3: "核心"}
    source_rows = [
        {"来源": source.name, "日摘": tier_name[source.digest_tier],
         "近 7 天收录": recent_by_source.get(source.name, 0),
         "最近文章": latest_by_source.get(source.name, "—")}
        for source in Config().rss_sources if source.enabled and source.digest_tier > 0
    ]
    source_rows.sort(key=lambda row: (-{"核心": 3, "常规": 2, "候选": 1}[row["日摘"]], row["来源"]))
    st.dataframe(source_rows, use_container_width=True, hide_index=True)
    st.caption("近 7 天为 0 只表示没有收录新文章；来源可在 CLI 查看抓取状态。")

    st.divider()

    # ── recent labels (deduped) ───────────────────────────────────────────────
    st.subheader("最近标记")
    recent = _dedup_recent(feedback, 12)
    if recent:
        rows = [
            {
                "标签": ("有价值" if r.get("label") == 1 else
                       "广告" if r.get("reason") == "advertisement" else "无价值"),
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
elif page == "标记文章":
    st.header("标记文章")
    st.caption("优先显示本期日摘，再显示最近收录的文章。也可在阅读页直接标记。")

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
                st.markdown(plain_markdown(summary[:500]) + ("…" if len(summary) > 500 else ""))

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
            if st.button("加入待保存"):
                _do_label(1, enqueue=True)
                st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: PAYLOAD QUEUE
# ══════════════════════════════════════════════════════════════════════════════
elif page == "待保存":
    st.header("待保存")

    queue = load_payload_queue()

    col_m, col_c = st.columns([3, 1])
    with col_m:
        st.metric("文章数", len(queue))
    with col_c:
        if queue and st.button("清空列表", type="secondary"):
            write_payload_queue([])
            st.rerun()

    if queue:
        st.divider()
        for i, item in enumerate(queue):
            with st.container(border=True):
                col_t, col_del = st.columns([5, 1])
                with col_t:
                    st.markdown(f"**{plain_markdown(item.get('title', '无标题'))}**")
                    st.caption(plain_markdown(item.get("source", "—")))
                    url = item.get("url", "")
                    if is_web_url(url):
                        st.link_button("阅读原文", url)
                with col_del:
                    if st.button("✕", key=f"del_{i}"):
                        remove_from_payload_queue(item.get("article_id", ""))
                        st.rerun()
    else:
        st.info("列表为空。可以从「标记文章」加入，也可以在下方搜索。")

    st.divider()
    st.subheader("搜索文章并加入")
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
                    st.markdown(
                        f"**{plain_markdown(a.get('title', '')[:70])}** — "
                        f"{plain_markdown(a.get('source', ''))}"
                    )
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

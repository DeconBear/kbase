"""All-library retrieval chat and session persistence."""
import copy
import json
import re
import threading
import time
import uuid
from pathlib import Path

from llm_config import call_chat_completion
import storage
from storage import (
    ARTICLES_DIR,
    CHAT_SESSIONS_DIR,
    CHAT_SESSIONS_INDEX,
    delete_chat_session_file,
    get_all_articles,
    list_chat_sessions,
    load_chat_session_file,
    save_chat_index,
    save_chat_session_file,
)

SESSION_LOCK = threading.Lock()
MAX_CONTEXT_CHARS = 38000
MAX_CHUNK_CHARS = 3600
MAX_SOURCES = 10
COMPACT_TOKEN_THRESHOLD = 22000
COMPACT_MESSAGE_THRESHOLD = 24
KEEP_RECENT_MESSAGES = 8


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _new_session_id():
    return f"session_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def _estimate_tokens(text):
    text = text or ""
    cn = len(re.findall(r"[\u4e00-\u9fff]", text))
    other = max(0, len(text) - cn)
    return int(cn / 1.5 + other / 3) + 1


from storage import get_all_notes, get_workspace_items, get_all_articles as load_index


def get_workspace_items_dict(workspace_id):
    if not workspace_id:
        return None
    items = get_workspace_items(workspace_id)
    return { (item['item_type'], item['item_id']) for item in items }


def _clean_message(message):
    role = str(message.get("role", "")).strip()
    if role not in {"user", "assistant"}:
        return None
    content = str(message.get("content", "")).strip()
    if not content:
        return None
    cleaned = {
        "role": role,
        "content": content,
        "ts": message.get("ts") or _now(),
    }
    if role == "assistant" and isinstance(message.get("sources"), list):
        cleaned["sources"] = message["sources"][:MAX_SOURCES]
    for key in ("provider_id", "model"):
        if message.get(key):
            cleaned[key] = str(message[key])
    return cleaned


def _make_session(title="新会话"):
    return {
        "id": _new_session_id(),
        "title": title or "新会话",
        "created_at": _now(),
        "updated_at": _now(),
        "messages": [],
        "memory_summary": "",
        "compacted_count": 0,
    }


def _load_store_unlocked():
    storage.ensure_directories()
    state = list_chat_sessions()
    sessions_meta = state.get("sessions") or []
    cleaned_sessions = []
    for meta in sessions_meta:
        if not isinstance(meta, dict):
            continue
        sid = str(meta.get("id", "")).strip()
        if not sid:
            continue
        session_file = load_chat_session_file(sid)
        messages = []
        for msg in session_file.get("messages") or []:
            if isinstance(msg, dict):
                cleaned = _clean_message(msg)
                if cleaned:
                    messages.append(cleaned)
        cleaned_sessions.append({
            "id": sid,
            "title": str(meta.get("title") or session_file.get("title") or "新会话"),
            "created_at": meta.get("created_at") or session_file.get("created_at") or _now(),
            "updated_at": meta.get("updated_at") or session_file.get("updated_at") or _now(),
            "messages": messages,
            "memory_summary": session_file.get("memory_summary") or meta.get("memory_summary") or "",
            "compacted_count": int(session_file.get("compacted_count") or meta.get("compacted_count") or 0),
        })
    if not cleaned_sessions:
        default = _make_session()
        cleaned_sessions.append(default)
        save_chat_session_file(default["id"], default)
    active = str(state.get("active_session_id") or cleaned_sessions[0]["id"])
    if active not in {s["id"] for s in cleaned_sessions}:
        active = cleaned_sessions[0]["id"]
    index = {
        "active_session_id": active,
        "sessions": [
            {
                "id": s["id"],
                "title": s["title"],
                "created_at": s["created_at"],
                "updated_at": s["updated_at"],
                "compacted_count": s["compacted_count"],
            }
            for s in cleaned_sessions
        ],
    }
    save_chat_index(index)
    return {"active_session_id": active, "sessions": cleaned_sessions}


def _save_session_unlocked(session: dict) -> None:
    save_chat_session_file(session["id"], session)
    state = list_chat_sessions()
    meta_list = state.get("sessions") or []
    existing_ids = {m.get("id") for m in meta_list}
    if session["id"] not in existing_ids:
        meta_list.insert(0, {
            "id": session["id"],
            "title": session.get("title", ""),
            "created_at": session.get("created_at", ""),
            "updated_at": session.get("updated_at", ""),
            "compacted_count": session.get("compacted_count", 0),
        })
    else:
        for m in meta_list:
            if m.get("id") == session["id"]:
                m["title"] = session.get("title", m.get("title", ""))
                m["updated_at"] = session.get("updated_at", m.get("updated_at", ""))
                m["compacted_count"] = session.get("compacted_count", m.get("compacted_count", 0))
                break
    meta_list.sort(key=lambda m: m.get("updated_at", ""), reverse=True)
    save_chat_index({"active_session_id": state.get("active_session_id", session["id"]), "sessions": meta_list})


def _delete_session_unlocked(sid: str) -> None:
    delete_chat_session_file(sid)
    state = list_chat_sessions()
    state["sessions"] = [m for m in (state.get("sessions") or []) if m.get("id") != sid]
    save_chat_index(state)


def _save_store_unlocked(store: dict) -> None:
    """Persist the per-session files and update the index. ``store`` is the
    in-memory representation that ``_load_store_unlocked`` returns."""
    for session in store.get("sessions") or []:
        save_chat_session_file(session["id"], session)
    state = list_chat_sessions()
    meta_list = []
    for session in store.get("sessions") or []:
        meta_list.append({
            "id": session["id"],
            "title": session.get("title", ""),
            "created_at": session.get("created_at", ""),
            "updated_at": session.get("updated_at", ""),
            "compacted_count": session.get("compacted_count", 0),
        })
    meta_list.sort(key=lambda m: m.get("updated_at", ""), reverse=True)
    save_chat_index({
        "active_session_id": store.get("active_session_id", ""),
        "sessions": meta_list,
    })


def _find_session(store, session_id):
    for session in store["sessions"]:
        if session["id"] == session_id:
            return session
    return None


def _public_session(session, include_messages=False):
    public = {
        "id": session["id"],
        "title": session.get("title") or "新会话",
        "created_at": session.get("created_at") or "",
        "updated_at": session.get("updated_at") or "",
        "message_count": len(session.get("messages") or []),
        "memory_summary": session.get("memory_summary") or "",
        "compacted_count": session.get("compacted_count") or 0,
    }
    if include_messages:
        public["messages"] = session.get("messages") or []
    return public


def list_sessions():
    with SESSION_LOCK:
        store = _load_store_unlocked()
        _save_store_unlocked(store)
        active = _find_session(store, store["active_session_id"])
        return {
            "active_session_id": store["active_session_id"],
            "sessions": [_public_session(s) for s in store["sessions"]],
            "active_session": _public_session(active, include_messages=True),
        }


def get_session(session_id):
    with SESSION_LOCK:
        store = _load_store_unlocked()
        session = _find_session(store, session_id)
        if not session:
            raise ValueError("Session not found")
        store["active_session_id"] = session_id
        _save_store_unlocked(store)
        return {
            "active_session_id": session_id,
            "sessions": [_public_session(s) for s in store["sessions"]],
            "session": _public_session(session, include_messages=True),
        }


def create_session(title="新会话"):
    with SESSION_LOCK:
        store = _load_store_unlocked()
        session = _make_session(title)
        store["sessions"].insert(0, session)
        store["active_session_id"] = session["id"]
        _save_store_unlocked(store)
        return {
            "active_session_id": session["id"],
            "sessions": [_public_session(s) for s in store["sessions"]],
            "session": _public_session(session, include_messages=True),
        }


def delete_session(session_id):
    with SESSION_LOCK:
        store = _load_store_unlocked()
        store["sessions"] = [s for s in store["sessions"] if s["id"] != session_id]
        if not store["sessions"]:
            store["sessions"].append(_make_session())
        if store["active_session_id"] == session_id:
            store["active_session_id"] = store["sessions"][0]["id"]
        active = _find_session(store, store["active_session_id"])
        _delete_session_unlocked(session_id)
        _save_store_unlocked(store)
        return {
            "active_session_id": store["active_session_id"],
            "sessions": [_public_session(s) for s in store["sessions"]],
            "active_session": _public_session(active, include_messages=True),
        }


def clear_session(session_id):
    with SESSION_LOCK:
        store = _load_store_unlocked()
        session = _find_session(store, session_id)
        if not session:
            raise ValueError("Session not found")
        session["messages"] = []
        session["memory_summary"] = ""
        session["compacted_count"] = 0
        session["updated_at"] = _now()
        store["active_session_id"] = session_id
        _save_store_unlocked(store)
        return {
            "active_session_id": session_id,
            "sessions": [_public_session(s) for s in store["sessions"]],
            "session": _public_session(session, include_messages=True),
        }


def _article_markdown_paths(article_id):
    folder = ARTICLES_DIR / article_id
    calibrated = folder / f"{article_id}_calibrated.md"
    parsed = folder / f"{article_id}.md"
    translated = folder / f"{article_id}_translated.md"
    candidates = []
    if calibrated.exists():
        candidates.append(("校准稿", calibrated))
    elif parsed.exists():
        candidates.append(("解析稿", parsed))
    if translated.exists():
        candidates.append(("译文", translated))
    seen = set()
    for variant, path in candidates:
        if not path.exists():
            continue
        try:
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield variant, path
        except Exception:
            continue

def _note_markdown_paths(note_id):
    path = storage.DATA_ROOT / "notes" / f"{note_id}.md"
    if path.exists():
        yield "笔记", path


def _split_long_text(text, max_chars=MAX_CHUNK_CHARS):
    parts = []
    current = ""
    for piece in re.split(r"(\n{2,})", text):
        if len(current) + len(piece) > max_chars and current.strip():
            parts.append(current.strip())
            current = piece
        else:
            current += piece
    if current.strip():
        parts.append(current.strip())

    out = []
    for part in parts:
        if len(part) <= max_chars:
            out.append(part)
        else:
            for start in range(0, len(part), max_chars):
                out.append(part[start:start + max_chars].strip())
    return [p for p in out if p]


def _chunk_markdown(text):
    sections = re.split(r"(?=^#{1,3}\s)", text, flags=re.M)
    chunks = []
    for section in sections:
        if not section.strip():
            continue
        heading = "前言"
        match = re.search(r"^#{1,3}\s+(.+)", section, flags=re.M)
        if match:
            heading = match.group(1).strip()
        for part in _split_long_text(section):
            chunks.append({"heading": heading, "text": part})
    return chunks


def _query_terms(query):
    lower = (query or "").lower()
    terms = set(re.findall(r"[a-z0-9][a-z0-9_.:+/-]{1,}", lower))
    for seg in re.findall(r"[\u4e00-\u9fff]{2,}", query or ""):
        if len(seg) <= 10:
            terms.add(seg)
        for i in range(0, max(0, len(seg) - 1)):
            terms.add(seg[i:i + 2])
    return [t for t in terms if len(t) >= 2]


def _plain_snippet(text, max_len=220):
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text or "")
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"`{1,3}", "", text)
    text = re.sub(r"#+\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len] + ("..." if len(text) > max_len else "")


def search_library(query, limit=MAX_SOURCES, context_chars=MAX_CONTEXT_CHARS, workspace_id=""):
    terms = _query_terms(query)
    
    ws_items = get_workspace_items_dict(workspace_id)
    
    idx = load_index()
    articles = idx.get("articles") if isinstance(idx, dict) else idx
    if not isinstance(articles, list):
        articles = []
    notes = get_all_notes() or []
    
    results = []
    source_no = 1

    # Search articles
    for article in articles:
        article_id = str(article.get("id") or "")
        if not article_id:
            continue
        if ws_items is not None and ("paper", article_id) not in ws_items:
            continue
            
        meta_text = " ".join([
            str(article.get("title") or ""),
            str(article.get("author") or ""),
            str(article.get("category") or ""),
            " ".join(article.get("tags") or []),
        ]).lower()
        for variant, path in _article_markdown_paths(article_id):
            try:
                md = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for chunk in _chunk_markdown(md):
                hay = (meta_text + "\n" + chunk["heading"] + "\n" + chunk["text"]).lower()
                score = 0
                if query and query.lower() in hay:
                    score += 12
                for term in terms:
                    count = hay.count(term.lower())
                    if count:
                        score += min(count, 8)
                        if term in meta_text:
                            score += 4
                        if term in chunk["heading"].lower():
                            score += 3
                if not terms:
                    score = 1
                if score <= 0:
                    continue
                results.append({
                    "source_id": f"S{source_no}",
                    "article_id": article_id,
                    "title": article.get("title") or article_id,
                    "author": article.get("author") or "",
                    "heading": chunk["heading"],
                    "variant": variant,
                    "score": score,
                    "text": chunk["text"],
                    "snippet": _plain_snippet(chunk["text"]),
                })
                source_no += 1

    # Search notes
    for note in notes:
        note_id = str(note.get("id") or "")
        if not note_id:
            continue
        if ws_items is not None and ("note", note_id) not in ws_items:
            continue
        meta_text = " ".join([
            str(note.get("title") or ""),
            str(note.get("folder") or ""),
            " ".join(note.get("tags") or []),
        ]).lower()
        for variant, path in _note_markdown_paths(note_id):
            try:
                from utils_yaml import parse_frontmatter
                _, md = parse_frontmatter(path)
            except Exception:
                continue
            for chunk in _chunk_markdown(md):
                hay = (meta_text + "\n" + chunk["heading"] + "\n" + chunk["text"]).lower()
                score = 0
                if query and query.lower() in hay:
                    score += 12
                for term in terms:
                    count = hay.count(term.lower())
                    if count:
                        score += min(count, 8)
                        if term in meta_text:
                            score += 4
                        if term in chunk["heading"].lower():
                            score += 3
                if not terms:
                    score = 1
                if score <= 0:
                    continue
                results.append({
                    "source_id": f"S{source_no}",
                    "article_id": note_id,
                    "title": note.get("title") or note_id,
                    "author": "Note",
                    "heading": chunk["heading"],
                    "variant": variant,
                    "score": score,
                    "text": chunk["text"],
                    "snippet": _plain_snippet(chunk["text"]),
                })
                source_no += 1

    results.sort(key=lambda item: item["score"], reverse=True)
    selected = []
    used_chars = 0
    seen_keys = set()
    for item in results:
        key = (item["article_id"], item["heading"], item["variant"])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        text = item["text"][:MAX_CHUNK_CHARS]
        if used_chars + len(text) > context_chars and selected:
            break
        item = dict(item)
        item["source_id"] = f"S{len(selected) + 1}"
        item["text"] = text
        selected.append(item)
        used_chars += len(text)
        if len(selected) >= limit:
            break
    return selected


def _format_sources_for_prompt(sources):
    if not sources:
        return "未检索到可用的论文片段。"
    parts = []
    for source in sources:
        parts.append(
            f"[{source['source_id']}] {source['title']} / {source['variant']} / {source['heading']}\n"
            f"{source['text']}"
        )
    return "\n\n---\n\n".join(parts)


def _compact_session_if_needed(session, provider_id="", model=""):
    messages = session.get("messages") or []
    total = sum(_estimate_tokens(m.get("content", "")) for m in messages)
    if total < COMPACT_TOKEN_THRESHOLD and len(messages) < COMPACT_MESSAGE_THRESHOLD:
        return session
    if len(messages) <= KEEP_RECENT_MESSAGES:
        return session

    old_messages = messages[:-KEEP_RECENT_MESSAGES]
    recent_messages = messages[-KEEP_RECENT_MESSAGES:]
    old_text = "\n\n".join(
        f"{m['role']}: {m['content']}" for m in old_messages
    )[-36000:]
    prior_summary = session.get("memory_summary") or ""
    prompt = f"""请把下面的多轮论文问答历史压缩为稳定记忆摘要。

要求：
- 保留用户长期关注的问题、已确认的论文事实、术语解释、结论和待跟进点。
- 不要加入新事实。
- 用中文输出，结构清晰，控制在 1200 字以内。

已有摘要：
{prior_summary or "无"}

待压缩历史：
{old_text}"""
    try:
        data = call_chat_completion(
            [{"role": "user", "content": prompt}],
            provider_id=provider_id,
            model=model,
            temperature=0.2,
            max_tokens=1800,
            timeout=180,
        )
        summary = ((data.get("choices") or [{}])[0].get("message", {}).get("content") or "").strip()
        if summary:
            session["memory_summary"] = summary
            session["messages"] = recent_messages
            session["compacted_count"] = int(session.get("compacted_count") or 0) + len(old_messages)
            session["compacted_at"] = _now()
    except Exception:
        pass
    return session


def _session_query(session, question):
    bits = [question]
    if session.get("memory_summary"):
        bits.append(session["memory_summary"])
    for msg in (session.get("messages") or [])[-6:]:
        if msg.get("role") == "user":
            bits.append(msg.get("content", ""))
    return "\n".join(bits)


def _sanitize_sources_for_client(sources):
    return [
        {
            "source_id": s["source_id"],
            "article_id": s["article_id"],
            "title": s["title"],
            "author": s.get("author", ""),
            "heading": s["heading"],
            "variant": s["variant"],
            "score": s["score"],
            "snippet": s["snippet"],
        }
        for s in sources
    ]


def prepare_library_question(question, session_id="", provider_id="", model="", workspace_id=""):
    question = str(question or "").strip()
    if not question:
        raise ValueError("Question is required")

    user_message = {
        "role": "user",
        "content": question,
        "ts": _now(),
        "provider_id": provider_id,
        "model": model,
    }

    with SESSION_LOCK:
        store = _load_store_unlocked()
        session = _find_session(store, session_id) if session_id else None
        if not session:
            session = _find_session(store, store["active_session_id"])
        if not session:
            session = _make_session()
            store["sessions"].insert(0, session)
        if not session.get("messages") and session.get("title") in {"新会话", ""}:
            session["title"] = question[:28] + ("..." if len(question) > 28 else "")
        session["messages"].append(user_message)
        session["updated_at"] = _now()

        # Compact inside the lock so concurrent requests don't race on
        # truncation. We compact the real session, persist it, and
        # also use it to build the prompt below.
        session = _compact_session_if_needed(
            session,
            provider_id=provider_id,
            model=model,
        )

        store["active_session_id"] = session["id"]
        _save_store_unlocked(store)
        working_session = copy.deepcopy(session)

    sources = search_library(_session_query(working_session, question), workspace_id=workspace_id)
    client_sources = _sanitize_sources_for_client(sources)

    system_prompt = f"""你是 KBase 的全库知识问答助手。你可以检索用户已经上传的论文和笔记 Markdown。
    
当前的工作空间限制为：{'已限制为选定文件' if workspace_id else '全局搜索'}

回答规则：
- 只依据“检索到的论文片段”和会话记忆回答；不要编造不存在的论文内容。
- 先直接回答用户问题，再解释依据、方法或推理。
- 需要比较多篇论文时，按论文分别说明。
- 引用证据时使用片段编号，如 [S1]、[S2]。不要使用不存在的编号。
- 如果检索片段不足以回答，明确说明缺少什么信息，并给出你能根据现有片段判断的部分。
- 使用用户提问的语言作答；中文问题用中文。
- 数学公式必须使用 $...$ 或 $$...$$，并确保每个 LaTeX 分隔符闭合且可由 KaTeX 渲染。
- 不要把 Markdown 表格、代码块、引用编号或普通文字放进公式分隔符。

会话记忆：
{working_session.get("memory_summary") or "无"}

检索到的论文片段：
{_format_sources_for_prompt(sources)}
"""

    api_messages = [{"role": "system", "content": system_prompt}]
    for msg in (working_session.get("messages") or [])[-12:]:
        api_messages.append({"role": msg["role"], "content": msg["content"]})

    return {
        "api_messages": api_messages,
        "sources": client_sources,
        "working_session": working_session,
        "provider_id": provider_id,
        "model": model,
    }


def finalize_library_answer(prepared, answer):
    answer = str(answer or "").strip()
    working_session = prepared["working_session"]
    provider_id = prepared.get("provider_id", "")
    model = prepared.get("model", "")
    client_sources = prepared.get("sources") or []

    assistant_message = {
        "role": "assistant",
        "content": answer,
        "ts": _now(),
        "provider_id": provider_id,
        "model": model,
        "sources": client_sources,
    }

    with SESSION_LOCK:
        store = _load_store_unlocked()
        session = _find_session(store, working_session["id"])
        if not session:
            session = working_session
            store["sessions"].insert(0, session)
        session["messages"] = session.get("messages") or []
        session["messages"].append(assistant_message)
        session["updated_at"] = _now()
        store["active_session_id"] = session["id"]
        store["sessions"].sort(key=lambda s: s.get("updated_at", ""), reverse=True)
        _save_store_unlocked(store)
        return {
            "status": "ok",
            "answer": answer,
            "sources": client_sources,
            "active_session_id": session["id"],
            "sessions": [_public_session(s) for s in store["sessions"]],
            "session": _public_session(session, include_messages=True),
        }


def ask_library_question(question, session_id="", provider_id="", model="", workspace_id=""):
    prepared = prepare_library_question(
        question,
        session_id=session_id,
        provider_id=provider_id,
        model=model,
        workspace_id=workspace_id
    )
    data = call_chat_completion(
        prepared["api_messages"],
        provider_id=provider_id,
        model=model,
        temperature=0.35,
        max_tokens=8192,
        timeout=300,
    )
    answer = ((data.get("choices") or [{}])[0].get("message", {}).get("content") or "").strip()
    return finalize_library_answer(prepared, answer)

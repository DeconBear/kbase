"""Feishu Bitable-inspired databases for KBase (standalone + note blocks)."""
from __future__ import annotations

import csv
import io
import json
import re
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import storage
from storage import _atomic_write_json, get_conn

_DB_ID_RE = re.compile(r"^db_[a-zA-Z0-9_-]{4,64}$")
_ROW_ID_RE = re.compile(r"^r_[a-zA-Z0-9_-]{4,64}$")
_COL_ID_RE = re.compile(r"^c_[a-zA-Z0-9_-]{4,64}$")
_VIEW_ID_RE = re.compile(r"^v_[a-zA-Z0-9_-]{4,64}$")
_LINK_REF_RE = re.compile(r"^(db_[a-zA-Z0-9_-]+):(r_[a-zA-Z0-9_-]+)$")

FIELD_TYPES = frozenset({
    "text", "longtext", "number", "currency", "percent", "progress", "rating",
    "date", "datetime", "select", "mselect", "checkbox", "url", "email", "phone",
    "person", "attachment", "autonumber", "link", "lookup", "rollup", "formula",
    "created_time", "modified_time", "ai_text",
})

READONLY_FIELD_TYPES = frozenset({"created_time", "modified_time", "lookup", "rollup", "formula", "autonumber"})

KANBAN_GROUP_TYPES = frozenset({"select", "mselect", "person", "text"})

FIELD_TYPE_LABELS: dict[str, str] = {
    "text": "单行文本",
    "longtext": "多行文本",
    "number": "数字",
    "currency": "货币",
    "percent": "百分比",
    "progress": "进度",
    "rating": "评分",
    "date": "日期",
    "datetime": "日期时间",
    "select": "单选",
    "mselect": "多选",
    "checkbox": "复选框",
    "url": "超链接",
    "email": "邮箱",
    "phone": "电话",
    "person": "人员",
    "attachment": "附件",
    "autonumber": "自动编号",
    "link": "关联",
    "lookup": "查找引用",
    "rollup": "汇总",
    "formula": "公式",
    "created_time": "创建时间",
    "modified_time": "修改时间",
    "ai_text": "AI 文本",
}

VIEW_TYPES = frozenset({"table", "kanban", "gallery", "calendar", "form"})

_HISTORY_KEEP = 20


def databases_dir() -> Path:
    return storage.KBASE_DIR / "databases"


def database_attachments_dir(db_id: str) -> Path:
    return storage.KBASE_DIR / "database_attachments" / validate_database_id(db_id)


def database_history_dir(db_id: str) -> Path:
    return databases_dir() / "history" / validate_database_id(db_id)


def public_field_types() -> list[dict[str, Any]]:
    return [
        {
            "id": t,
            "label": FIELD_TYPE_LABELS.get(t, t),
            "kanbanGroup": t in KANBAN_GROUP_TYPES,
            "readonly": t in READONLY_FIELD_TYPES,
        }
        for t in sorted(FIELD_TYPES)
    ]


def validate_database_id(value: str) -> str:
    db_id = (value or "").strip()
    if not _DB_ID_RE.match(db_id):
        raise ValueError("Invalid database id")
    return db_id


def _ensure_dir() -> None:
    databases_dir().mkdir(parents=True, exist_ok=True)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _db_path(db_id: str) -> Path:
    return databases_dir() / f"{validate_database_id(db_id)}.json"


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _select_options(names: list[str]) -> list[dict[str, str]]:
    colors = ("1", "2", "3", "4", "5", "6", "7", "8")
    return [{"name": n, "color": colors[i % len(colors)]} for i, n in enumerate(names)]


def _default_cell_value(col_type: str) -> Any:
    if col_type == "checkbox":
        return False
    if col_type in ("mselect", "link", "attachment"):
        return []
    if col_type in ("number", "currency", "percent", "progress", "rating"):
        return None
    if col_type in ("created_time", "modified_time", "autonumber"):
        return None
    return ""


def _column_defaults(col_type: str, name: str) -> dict[str, Any]:
    col: dict[str, Any] = {
        "id": _new_id("c"),
        "name": name,
        "type": col_type,
        "width": 140,
    }
    if col_type == "text":
        col["width"] = 200
    if col_type == "longtext":
        col["width"] = 240
    if col_type == "select":
        if name == "状态":
            col["options"] = _select_options(["未开始", "进行中", "待审核", "完成", "已搁置"])
        elif name == "优先级":
            col["options"] = _select_options(["P0 紧急", "P1 高", "P2 中", "P3 低"])
        else:
            col["options"] = _select_options(["选项1", "选项2", "选项3"])
    if col_type == "mselect":
        col["options"] = _select_options(["需求", "缺陷", "优化", "文档"])
    if col_type == "rating":
        col["max"] = 5
    if col_type == "progress":
        col["max"] = 100
    if col_type == "currency":
        col["currency"] = "CNY"
    if col_type == "link":
        col["linkDatabase"] = ""
        col["bidirectional"] = False
        col["reverseColumn"] = ""
    if col_type == "lookup":
        col["linkColumn"] = ""
        col["lookupColumn"] = ""
    if col_type == "rollup":
        col["linkColumn"] = ""
        col["rollupColumn"] = ""
        col["rollupFn"] = "count"
    if col_type == "formula":
        col["expression"] = ""
    if col_type == "ai_text":
        col["aiPrompt"] = "根据本行其他字段生成摘要"
    return col


def _default_database(db_id: str, name: str = "Untitled") -> dict[str, Any]:
    title_col = _column_defaults("text", "标题")
    title_col["width"] = 220
    status_col = _column_defaults("select", "状态")
    status_col["width"] = 120
    priority_col = _column_defaults("select", "优先级")
    priority_col["width"] = 110
    owner_col = _column_defaults("person", "负责人")
    due_col = _column_defaults("date", "截止日期")
    progress_col = _column_defaults("progress", "进度")
    tags_col = _column_defaults("mselect", "标签")
    rating_col = _column_defaults("rating", "评分")
    note_col = _column_defaults("longtext", "备注")
    link_col = _column_defaults("url", "链接")
    created_col = _column_defaults("created_time", "创建时间")
    modified_col = _column_defaults("modified_time", "修改时间")

    columns = [
        title_col, status_col, priority_col, owner_col, due_col,
        progress_col, tags_col, rating_col, note_col, link_col,
        created_col, modified_col,
    ]
    table_view_id = _new_id("v")
    return {
        "spec": 3,
        "id": db_id,
        "name": name or "Untitled",
        "icon": "📊",
        "description": "",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "columns": columns,
        "rows": [],
        "views": [
            {
                "id": table_view_id,
                "name": "表格",
                "type": "table",
                "filters": [],
                "sorts": [],
                "columnWidths": {},
                "hiddenColumns": [],
                "columnOrder": [],
                "groupBy": "",
                "frozenColumns": 1,
                "conditionalFormats": [],
            },
            {
                "id": _new_id("v"),
                "name": "看板 · 状态",
                "type": "kanban",
                "groupColumn": status_col["id"],
                "filters": [],
                "sorts": [],
                "kanbanOrder": {},
            },
            {
                "id": _new_id("v"),
                "name": "看板 · 优先级",
                "type": "kanban",
                "groupColumn": priority_col["id"],
                "filters": [],
                "sorts": [],
                "kanbanOrder": {},
            },
            {
                "id": _new_id("v"),
                "name": "画廊",
                "type": "gallery",
                "coverColumn": title_col["id"],
                "cardFields": [status_col["id"], priority_col["id"], due_col["id"]],
                "filters": [],
                "sorts": [],
            },
            {
                "id": _new_id("v"),
                "name": "日历",
                "type": "calendar",
                "dateColumn": due_col["id"],
                "filters": [],
                "sorts": [],
            },
            {
                "id": _new_id("v"),
                "name": "表单",
                "type": "form",
                "formFields": [c["id"] for c in columns if c["type"] not in READONLY_FIELD_TYPES],
                "filters": [],
                "sorts": [],
            },
        ],
        "viewID": table_view_id,
    }


def _save_history_snapshot(db_id: str, data: dict[str, Any]) -> None:
    hist_dir = database_history_dir(db_id)
    hist_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = hist_dir / f"{stamp}.json"
    try:
        _atomic_write_json(path, data)
        files = sorted(hist_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[_HISTORY_KEEP:]:
            try:
                old.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _sync_database_index(data: dict[str, Any]) -> None:
    db_id = str(data.get("id") or "")
    if not db_id:
        return
    rel_path = f"databases/{db_id}.json"
    row_count = len(data.get("rows") or [])
    view_count = len(data.get("views") or [])
    updated_at = data.get("updated_at") or _now_iso()
    try:
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO databases (id, name, icon, description, file_path, row_count, view_count, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     name=excluded.name, icon=excluded.icon, description=excluded.description,
                     file_path=excluded.file_path, row_count=excluded.row_count,
                     view_count=excluded.view_count, updated_at=excluded.updated_at""",
                (
                    db_id,
                    data.get("name") or "Untitled",
                    data.get("icon") or "📊",
                    data.get("description") or "",
                    rel_path,
                    row_count,
                    view_count,
                    updated_at,
                ),
            )
            conn.execute("DELETE FROM database_cell_index WHERE database_id = ?", (db_id,))
            for row in data.get("rows") or []:
                rid = str(row.get("id") or "")
                cells = row.get("cells") or {}
                for cid, val in cells.items():
                    text_val = _cell_to_search_text(val)
                    if not text_val:
                        continue
                    conn.execute(
                        """INSERT INTO database_cell_index (database_id, row_id, column_id, text_value)
                           VALUES (?, ?, ?, ?)""",
                        (db_id, rid, cid, text_val[:2000]),
                    )
            conn.commit()
    except Exception:
        pass


def _cell_to_search_text(val: Any) -> str:
    if val is None or val is False:
        return ""
    if isinstance(val, list):
        parts = []
        for item in val:
            if isinstance(item, dict):
                parts.append(str(item.get("name") or item.get("url") or ""))
            else:
                parts.append(str(item))
        return " ".join(p for p in parts if p)
    return str(val)


def reindex_all_databases() -> int:
    """Rebuild SQLite catalog from JSON files on disk."""
    _ensure_dir()
    count = 0
    for path in sorted(databases_dir().glob("db_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("id"):
                _sync_database_index(data)
                count += 1
        except Exception:
            continue
    return count


def import_legacy_databases(from_root: Path | None = None) -> int:
    """Copy db_*.json from another data root into the active databases dir."""
    _ensure_dir()
    source = (from_root or storage.default_data_root()) / ".kbase" / "databases"
    if not source.is_dir() or source.resolve() == databases_dir().resolve():
        return 0
    imported = 0
    for path in source.glob("db_*.json"):
        dest = databases_dir() / path.name
        if dest.exists():
            continue
        dest.write_bytes(path.read_bytes())
        try:
            data = json.loads(dest.read_text(encoding="utf-8"))
            _sync_database_index(data)
        except Exception:
            pass
        imported += 1
    return imported


def list_databases() -> list[dict[str, Any]]:
    _ensure_dir()
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT id, name, icon, description, row_count, view_count, updated_at FROM databases ORDER BY updated_at DESC"
            ).fetchall()
            if rows:
                return [
                    {
                        "id": r["id"],
                        "name": r["name"] or "Untitled",
                        "icon": r["icon"] or "📊",
                        "description": r["description"] or "",
                        "row_count": r["row_count"] or 0,
                        "view_count": r["view_count"] or 1,
                        "updated_at": r["updated_at"] or "",
                    }
                    for r in rows
                ]
    except Exception:
        pass
    out: list[dict[str, Any]] = []
    for path in sorted(databases_dir().glob("db_*.json")):
        try:
            data = load_database(path.stem)
            out.append({
                "id": data["id"],
                "name": data.get("name") or "Untitled",
                "icon": data.get("icon") or "📊",
                "description": data.get("description") or "",
                "row_count": len(data.get("rows") or []),
                "view_count": len(data.get("views") or []),
                "updated_at": data.get("updated_at") or "",
            })
        except Exception:
            continue
    return out


def load_database(db_id: str) -> dict[str, Any]:
    _ensure_dir()
    path = _db_path(db_id)
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {db_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("id") != db_id:
        raise ValueError("Corrupt database file")
    return data


def save_database(data: dict[str, Any], *, snapshot: bool = True) -> dict[str, Any]:
    db_id = validate_database_id(str(data.get("id") or ""))
    _ensure_dir()
    path = _db_path(db_id)
    if snapshot and path.exists():
        try:
            _save_history_snapshot(db_id, load_database(db_id))
        except Exception:
            pass
    payload = deepcopy(data)
    payload["id"] = db_id
    payload["updated_at"] = _now_iso()
    _atomic_write_json(path, payload)
    _sync_database_index(payload)
    return payload


def create_database(name: str = "Untitled") -> dict[str, Any]:
    db_id = _new_id("db")
    data = _default_database(db_id, name)
    return save_database(data, snapshot=False)


def delete_database(db_id: str) -> None:
    path = _db_path(db_id)
    if path.exists():
        path.unlink()
    try:
        with get_conn() as conn:
            conn.execute("DELETE FROM database_cell_index WHERE database_id = ?", (db_id,))
            conn.execute("DELETE FROM databases WHERE id = ?", (db_id,))
            conn.commit()
    except Exception:
        pass


def _column_map(db: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["id"]: c for c in db.get("columns") or [] if c.get("id")}


def _active_view(db: dict[str, Any], view_id: str | None = None) -> dict[str, Any]:
    views = db.get("views") or []
    if not views:
        view = {"id": _new_id("v"), "name": "表格", "type": "table", "filters": [], "sorts": []}
        db["views"] = [view]
        db["viewID"] = view["id"]
        return view
    if view_id:
        for v in views:
            if v.get("id") == view_id:
                return v
    active = db.get("viewID")
    for v in views:
        if v.get("id") == active:
            return v
    return views[0]


def _ordered_columns(db: dict[str, Any], view: dict[str, Any]) -> list[dict[str, Any]]:
    columns = list(db.get("columns") or [])
    order = view.get("columnOrder") or []
    hidden = set(view.get("hiddenColumns") or [])
    if order:
        col_map = {c["id"]: c for c in columns}
        ordered = [col_map[cid] for cid in order if cid in col_map]
        for c in columns:
            if c["id"] not in order:
                ordered.append(c)
        columns = ordered
    if hidden:
        columns = [c for c in columns if c.get("id") not in hidden]
    return columns


def _apply_sorts(rows: list[dict[str, Any]], sorts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not sorts:
        return rows
    out = list(rows)

    def sort_key(row: dict[str, Any]) -> tuple:
        keys: list[Any] = []
        cells = row.get("cells") or {}
        for spec in sorts:
            col = spec.get("column") or spec.get("id")
            val = cells.get(col, "")
            if isinstance(val, list):
                val = ",".join(str(x) for x in val)
            asc = (spec.get("order") or "asc").lower() != "desc"
            keys.append((0, str(val).lower()) if asc else (1, str(val).lower()))
        return tuple(keys)

    try:
        out.sort(key=sort_key)
    except Exception:
        pass
    return out


def _match_filter(got: Any, op: str, want: Any) -> bool:
    if isinstance(got, list):
        got_s = ",".join(str(x) for x in got)
    elif got is None:
        got_s = ""
    else:
        got_s = str(got)
    want_s = str(want) if want is not None else ""
    op = (op or "contains").lower()
    if op == "eq":
        return got_s == want_s
    if op == "neq":
        return got_s != want_s
    if op == "contains":
        return want_s.lower() in got_s.lower()
    if op == "empty":
        return not got_s.strip()
    if op == "not_empty":
        return bool(got_s.strip())
    if op in ("gt", "gte", "lt", "lte"):
        try:
            g = float(got_s) if got_s else 0.0
            w = float(want_s) if want_s else 0.0
        except ValueError:
            g, w = got_s, want_s
        if op == "gt":
            return g > w
        if op == "gte":
            return g >= w
        if op == "lt":
            return g < w
        if op == "lte":
            return g <= w
    return True


def _apply_filters(rows: list[dict[str, Any]], filters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not filters:
        return rows
    out: list[dict[str, Any]] = []
    for row in rows:
        cells = row.get("cells") or {}
        keep = True
        for f in filters:
            col = f.get("column") or f.get("columnId") or f.get("column_id") or f.get("id")
            if not _match_filter(cells.get(col, ""), f.get("op") or "contains", f.get("value", "")):
                keep = False
                break
        if keep:
            out.append(row)
    return out


def _next_autonumber(db: dict[str, Any], col_id: str) -> int:
    max_n = 0
    for row in db.get("rows") or []:
        val = (row.get("cells") or {}).get(col_id)
        try:
            max_n = max(max_n, int(val))
        except (TypeError, ValueError):
            pass
    return max_n + 1


def _parse_link_ref(ref: str) -> tuple[str, str] | None:
    m = _LINK_REF_RE.match(str(ref).strip())
    if not m:
        return None
    return m.group(1), m.group(2)


def _load_linked_row(db_id: str, row_id: str) -> dict[str, Any] | None:
    try:
        db = load_database(db_id)
    except (FileNotFoundError, ValueError):
        return None
    for row in db.get("rows") or []:
        if row.get("id") == row_id:
            return row
    return None


def _resolve_lookup(db: dict[str, Any], col: dict[str, Any], row: dict[str, Any]) -> Any:
    link_col_id = col.get("linkColumn") or ""
    lookup_col_id = col.get("lookupColumn") or ""
    if not link_col_id or not lookup_col_id:
        return ""
    refs = (row.get("cells") or {}).get(link_col_id, [])
    if not isinstance(refs, list):
        refs = [refs] if refs else []
    values: list[str] = []
    for ref in refs:
        parsed = _parse_link_ref(str(ref))
        if not parsed:
            continue
        target_db, target_row = parsed
        target = _load_linked_row(target_db, target_row)
        if not target:
            continue
        val = (target.get("cells") or {}).get(lookup_col_id, "")
        if isinstance(val, list):
            values.extend(str(x) for x in val)
        elif val not in ("", None):
            values.append(str(val))
    return ", ".join(values)


def _resolve_rollup(db: dict[str, Any], col: dict[str, Any], row: dict[str, Any]) -> Any:
    link_col_id = col.get("linkColumn") or ""
    rollup_col_id = col.get("rollupColumn") or ""
    fn = (col.get("rollupFn") or "count").lower()
    if not link_col_id:
        return None if fn != "count" else 0
    refs = (row.get("cells") or {}).get(link_col_id, [])
    if not isinstance(refs, list):
        refs = [refs] if refs else []
    nums: list[float] = []
    count = 0
    for ref in refs:
        parsed = _parse_link_ref(str(ref))
        if not parsed:
            continue
        target = _load_linked_row(parsed[0], parsed[1])
        if not target:
            continue
        count += 1
        if rollup_col_id:
            val = (target.get("cells") or {}).get(rollup_col_id)
            try:
                if val not in ("", None):
                    nums.append(float(val))
            except (TypeError, ValueError):
                pass
    if fn == "count":
        return count
    if fn == "sum":
        return sum(nums) if nums else 0
    if fn == "avg":
        return sum(nums) / len(nums) if nums else None
    if fn == "min":
        return min(nums) if nums else None
    if fn == "max":
        return max(nums) if nums else None
    return count


def _formula_funcs() -> dict[str, Any]:
    def IF(cond: Any, a: Any, b: Any) -> Any:
        return a if cond else b

    def CONCAT(*args: Any) -> str:
        return "".join(str(a) for a in args)

    return {"IF": IF, "CONCAT": CONCAT, "ABS": abs, "MIN": min, "MAX": max, "ROUND": round}


def _resolve_formula(db: dict[str, Any], col: dict[str, Any], row: dict[str, Any]) -> Any:
    expr = str(col.get("expression") or "").strip()
    if not expr:
        return ""
    col_map = _column_map(db)
    cells = row.get("cells") or {}
    env: dict[str, Any] = dict(_formula_funcs())
    for cid, cdef in col_map.items():
        key = re.sub(r"[^\w]", "_", cdef.get("name") or cid)
        val = cells.get(cid, "")
        if isinstance(val, list):
            val = ",".join(str(x) for x in val)
        env[key] = val
    try:
        if expr.startswith("="):
            expr = expr[1:]
        return eval(expr, {"__builtins__": {}}, env)  # noqa: S307
    except Exception:
        return ""


def _resolve_computed_cells(db: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    cells = dict(row.get("cells") or {})
    meta = row.get("meta") or {}
    for col in db.get("columns") or []:
        ctype = col.get("type") or ""
        cid = col.get("id") or ""
        if ctype == "created_time":
            cells[cid] = meta.get("created_at") or ""
        elif ctype == "modified_time":
            cells[cid] = meta.get("updated_at") or ""
        elif ctype == "lookup":
            cells[cid] = _resolve_lookup(db, col, row)
        elif ctype == "rollup":
            cells[cid] = _resolve_rollup(db, col, row)
        elif ctype == "formula":
            cells[cid] = _resolve_formula(db, col, row)
    return cells


def _sync_bidirectional_links(
    db: dict[str, Any],
    row_id: str,
    col_id: str,
    old_refs: list[str],
    new_refs: list[str],
) -> None:
    col = _column_map(db).get(col_id) or {}
    if not col.get("bidirectional"):
        return
    target_db_id = str(col.get("linkDatabase") or "")
    reverse_col = str(col.get("reverseColumn") or "")
    if not target_db_id or not reverse_col:
        return
    back_ref = f"{db['id']}:{row_id}"
    old_set = set(old_refs)
    new_set = set(new_refs)
    for removed in old_set - new_set:
        parsed = _parse_link_ref(removed)
        if not parsed or parsed[0] != target_db_id:
            continue
        try:
            target_db = load_database(parsed[0])
            for trow in target_db.get("rows") or []:
                if trow.get("id") != parsed[1]:
                    continue
                refs = list((trow.get("cells") or {}).get(reverse_col) or [])
                if back_ref in refs:
                    refs.remove(back_ref)
                    trow.setdefault("cells", {})[reverse_col] = refs
                    trow.setdefault("meta", {})["updated_at"] = _now_iso()
            save_database(target_db)
        except (FileNotFoundError, ValueError):
            pass
    for added in new_set - old_set:
        parsed = _parse_link_ref(added)
        if not parsed or parsed[0] != target_db_id:
            continue
        try:
            target_db = load_database(parsed[0])
            for trow in target_db.get("rows") or []:
                if trow.get("id") != parsed[1]:
                    continue
                refs = list((trow.get("cells") or {}).get(reverse_col) or [])
                if back_ref not in refs:
                    refs.append(back_ref)
                    trow.setdefault("cells", {})[reverse_col] = refs
                    trow.setdefault("meta", {})["updated_at"] = _now_iso()
            save_database(target_db)
        except (FileNotFoundError, ValueError):
            pass


def _kanban_column_labels(col_def: dict[str, Any] | None) -> list[str]:
    if not col_def:
        return []
    ctype = col_def.get("type") or ""
    if ctype in ("select", "mselect"):
        return [str(o.get("name") or "") for o in col_def.get("options") or [] if o.get("name")]
    return []


def _apply_kanban_order(
    buckets: dict[str, list[dict[str, Any]]], kanban_order: dict[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    if not kanban_order:
        return buckets
    out: dict[str, list[dict[str, Any]]] = {}
    for key, rows in buckets.items():
        order = kanban_order.get(key) or []
        if not order:
            out[key] = rows
            continue
        row_map = {r["id"]: r for r in rows}
        sorted_rows = [row_map[rid] for rid in order if rid in row_map]
        for r in rows:
            if r["id"] not in order:
                sorted_rows.append(r)
        out[key] = sorted_rows
    return out


def _render_kanban_groups(
    db: dict[str, Any], view: dict[str, Any], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    group_col_id = view.get("groupColumn") or ""
    col_map = _column_map(db)
    col_def = col_map.get(group_col_id)
    ctype = (col_def or {}).get("type") or ""
    labels = _kanban_column_labels(col_def)
    if not labels and ctype in KANBAN_GROUP_TYPES:
        seen: list[str] = []
        for row in rows:
            val = (row.get("cells") or {}).get(group_col_id, "")
            if isinstance(val, list):
                for item in val:
                    s = str(item).strip()
                    if s and s not in seen:
                        seen.append(s)
            else:
                s = str(val).strip()
                if s and s not in seen:
                    seen.append(s)
        labels = seen
    labels.append("__ungrouped__")
    buckets: dict[str, list[dict[str, Any]]] = {k: [] for k in labels}

    for row in rows:
        resolved = _resolve_computed_cells(db, row)
        row_copy = dict(row)
        row_copy["cells"] = resolved
        val = resolved.get(group_col_id, "")
        keys: list[str] = []
        if ctype == "mselect" and isinstance(val, list):
            keys = [str(x) for x in val if str(x).strip()]
        elif val not in ("", None, False):
            keys = [str(val)]
        if not keys:
            buckets["__ungrouped__"].append(row_copy)
            continue
        for key in keys:
            if key not in buckets:
                buckets[key] = []
            buckets[key].append(row_copy)

    buckets = _apply_kanban_order(buckets, view.get("kanbanOrder") or {})

    columns_out: list[dict[str, Any]] = []
    for key in labels:
        name = "未分组" if key == "__ungrouped__" else key
        opt_color = ""
        if col_def and col_def.get("type") == "select":
            for o in col_def.get("options") or []:
                if o.get("name") == key:
                    opt_color = str(o.get("color") or "")
                    break
        columns_out.append({
            "id": key,
            "name": name,
            "color": opt_color,
            "rows": buckets.get(key) or [],
        })
    for key, items in buckets.items():
        if key in labels:
            continue
        columns_out.append({"id": key, "name": key or "未分组", "color": "", "rows": items})
    return {
        "groupColumn": group_col_id,
        "groupType": ctype,
        "columns": columns_out,
    }


def _cell_cover_value(col_def: dict[str, Any] | None, val: Any) -> str:
    if val in ("", None):
        return ""
    ctype = (col_def or {}).get("type") or "text"
    if ctype == "attachment" and isinstance(val, list) and val:
        first = val[0]
        if isinstance(first, dict):
            url = str(first.get("url") or "")
            if url.startswith("/") or url.startswith("http"):
                return url
            return str(first.get("name") or "")
        return str(first)
    if ctype == "url" and val:
        return str(val)
    return ""


def _render_gallery(
    db: dict[str, Any], view: dict[str, Any], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    col_map = _column_map(db)
    columns = db.get("columns") or []
    title_col_id = columns[0]["id"] if columns else ""
    cover_col_id = view.get("coverColumn") or title_col_id
    card_field_ids = view.get("cardFields") or []
    if not card_field_ids:
        card_field_ids = [c["id"] for c in columns[1:4] if c.get("id")]

    items: list[dict[str, Any]] = []
    for row in rows:
        cells = _resolve_computed_cells(db, row)
        cover_val = cells.get(cover_col_id, "")
        cover = _cell_cover_value(col_map.get(cover_col_id), cover_val)
        if not cover and title_col_id:
            cover = str(cells.get(title_col_id) or "")[:1]
        fields_out = []
        for fid in card_field_ids:
            cdef = col_map.get(fid)
            if not cdef:
                continue
            fields_out.append({
                "id": fid,
                "name": cdef.get("name") or fid,
                "type": cdef.get("type") or "text",
                "value": cells.get(fid, ""),
            })
        items.append({
            "row": {**row, "cells": cells},
            "title": cells.get(title_col_id, "") if title_col_id else "",
            "cover": cover,
            "coverText": str(cells.get(title_col_id) or "未命名")[:2],
            "fields": fields_out,
        })
    return {
        "coverColumn": cover_col_id,
        "titleColumn": title_col_id,
        "items": items,
    }


def _render_calendar(
    db: dict[str, Any], view: dict[str, Any], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    date_col = view.get("dateColumn") or ""
    columns = db.get("columns") or []
    if not date_col and columns:
        for c in columns:
            if c.get("type") in ("date", "datetime"):
                date_col = c["id"]
                break
    buckets: dict[str, list[dict[str, Any]]] = {}
    title_col = columns[0]["id"] if columns else ""
    for row in rows:
        cells = _resolve_computed_cells(db, row)
        raw = str(cells.get(date_col) or "").strip()
        day = raw[:10] if raw else "__nodate__"
        buckets.setdefault(day, []).append({
            "row": {**row, "cells": cells},
            "title": cells.get(title_col, "") if title_col else row.get("id"),
            "date": raw,
        })
    days = sorted(buckets.keys(), key=lambda d: (d == "__nodate__", d))
    return {
        "dateColumn": date_col,
        "days": [{"date": d, "label": "无日期" if d == "__nodate__" else d, "items": buckets[d]} for d in days],
    }


def _render_form(db: dict[str, Any], view: dict[str, Any]) -> dict[str, Any]:
    col_map = _column_map(db)
    field_ids = view.get("formFields") or [c["id"] for c in db.get("columns") or [] if c.get("id")]
    fields = []
    for fid in field_ids:
        cdef = col_map.get(fid)
        if not cdef or cdef.get("type") in READONLY_FIELD_TYPES:
            continue
        fields.append({
            "id": fid,
            "name": cdef.get("name") or fid,
            "type": cdef.get("type") or "text",
            "options": cdef.get("options") or [],
        })
    return {"fields": fields}


def _render_table_groups(
    db: dict[str, Any], view: dict[str, Any], rows: list[dict[str, Any]]
) -> list[dict[str, Any]] | None:
    group_by = view.get("groupBy") or ""
    if not group_by:
        return None
    col_map = _column_map(db)
    col_def = col_map.get(group_by)
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        cells = _resolve_computed_cells(db, row)
        val = cells.get(group_by, "")
        if isinstance(val, list):
            key = ", ".join(str(x) for x in val) or "未分组"
        else:
            key = str(val).strip() or "未分组"
        groups.setdefault(key, []).append({**row, "cells": cells})
    out = []
    for key in sorted(groups.keys(), key=lambda k: (k == "未分组", k)):
        color = ""
        if col_def and col_def.get("type") == "select":
            for o in col_def.get("options") or []:
                if o.get("name") == key:
                    color = str(o.get("color") or "")
                    break
        out.append({"id": key, "name": key, "color": color, "rows": groups[key]})
    return out


def render_database(db_id: str, view_id: str | None = None, *, query: str = "") -> dict[str, Any]:
    db = load_database(db_id)
    view = _active_view(db, view_id)
    rows = list(db.get("rows") or [])
    if query.strip():
        q = query.strip().lower()
        filtered = []
        for row in rows:
            cells = _resolve_computed_cells(db, row)
            hay = " ".join(_cell_to_search_text(v) for v in cells.values()).lower()
            if q in hay:
                filtered.append(row)
        rows = filtered
    rows = _apply_filters(rows, view.get("filters") or [])
    rows = _apply_sorts(rows, view.get("sorts") or [])
    display_rows = []
    for row in rows:
        display_rows.append({**row, "cells": _resolve_computed_cells(db, row)})
    columns = _ordered_columns(db, view)
    result: dict[str, Any] = {
        "id": db["id"],
        "name": db.get("name") or "Untitled",
        "icon": db.get("icon") or "📊",
        "description": db.get("description") or "",
        "view": view,
        "views": db.get("views") or [],
        "columns": columns,
        "allColumns": db.get("columns") or [],
        "rows": display_rows,
        "fieldTypes": public_field_types(),
    }
    vtype = view.get("type") or "table"
    if vtype == "kanban":
        result["kanban"] = _render_kanban_groups(db, view, rows)
    elif vtype == "gallery":
        result["gallery"] = _render_gallery(db, view, rows)
    elif vtype == "calendar":
        result["calendar"] = _render_calendar(db, view, rows)
    elif vtype == "form":
        result["form"] = _render_form(db, view)
    else:
        groups = _render_table_groups(db, view, rows)
        if groups:
            result["groups"] = groups
    return result


def search_databases(query: str, *, limit: int = 50) -> list[dict[str, Any]]:
    q = (query or "").strip().lower()
    if not q:
        return []
    out: list[dict[str, Any]] = []
    try:
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT database_id, row_id, column_id, text_value
                   FROM database_cell_index
                   WHERE lower(text_value) LIKE ?
                   LIMIT ?""",
                (f"%{q}%", limit * 3),
            ).fetchall()
            seen: set[str] = set()
            for r in rows:
                key = f"{r['database_id']}:{r['row_id']}"
                if key in seen:
                    continue
                seen.add(key)
                try:
                    db = load_database(r["database_id"])
                    db_name = db.get("name") or r["database_id"]
                except Exception:
                    db_name = r["database_id"]
                out.append({
                    "database_id": r["database_id"],
                    "database_name": db_name,
                    "row_id": r["row_id"],
                    "column_id": r["column_id"],
                    "snippet": r["text_value"][:120],
                })
                if len(out) >= limit:
                    break
    except Exception:
        pass
    if out:
        return out
    for meta in list_databases():
        if q in (meta.get("name") or "").lower():
            out.append({
                "database_id": meta["id"],
                "database_name": meta.get("name") or meta["id"],
                "row_id": "",
                "column_id": "",
                "snippet": meta.get("description") or "",
            })
    return out[:limit]


def export_database_csv(db_id: str, view_id: str | None = None) -> str:
    data = render_database(db_id, view_id)
    columns = data.get("columns") or []
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([c.get("name") or c.get("id") for c in columns])
    for row in data.get("rows") or []:
        cells = row.get("cells") or {}
        writer.writerow([_cell_to_search_text(cells.get(c["id"], "")) for c in columns])
    return buf.getvalue()


def import_database_csv(
    db_id: str,
    csv_text: str,
    *,
    mode: str = "append",
) -> dict[str, Any]:
    db = load_database(db_id)
    reader = csv.reader(io.StringIO(csv_text))
    rows_iter = iter(reader)
    try:
        header = next(rows_iter)
    except StopIteration:
        raise ValueError("Empty CSV")
    col_map = {c.get("name"): c for c in db.get("columns") or []}
    if mode == "replace":
        db["rows"] = []
        save_database(db)
    added = 0
    for line in rows_iter:
        if not any(cell.strip() for cell in line):
            continue
        cells: dict[str, Any] = {}
        for i, val in enumerate(line):
            if i >= len(header):
                break
            col = col_map.get(header[i])
            if col:
                cells[col["id"]] = val
        add_row(db_id, cells)
        added += 1
    return {"added": added}


def list_database_history(db_id: str) -> list[dict[str, Any]]:
    hist_dir = database_history_dir(db_id)
    if not hist_dir.exists():
        return []
    out = []
    for path in sorted(hist_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        out.append({
            "id": path.stem,
            "path": str(path.name),
            "mtime": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        })
    return out


def restore_database_history(db_id: str, snapshot_id: str) -> dict[str, Any]:
    hist_dir = database_history_dir(db_id)
    path = hist_dir / f"{snapshot_id}.json"
    if not path.exists():
        raise FileNotFoundError("Snapshot not found")
    data = json.loads(path.read_text(encoding="utf-8"))
    return save_database(data)


def batch_delete_rows(db_id: str, row_ids: list[str]) -> int:
    db = load_database(db_id)
    want = set(row_ids)
    before = len(db.get("rows") or [])
    db["rows"] = [r for r in db.get("rows") or [] if r.get("id") not in want]
    deleted = before - len(db.get("rows") or [])
    if deleted:
        save_database(db)
    return deleted


def _pick_kanban_column(db: dict[str, Any], group_column: str = "") -> str:
    if group_column:
        col_map = _column_map(db)
        if group_column in col_map and col_map[group_column].get("type") in KANBAN_GROUP_TYPES:
            return group_column
    for c in db.get("columns") or []:
        if c.get("type") in KANBAN_GROUP_TYPES:
            return c["id"]
    return ""


def _pick_date_column(db: dict[str, Any], date_column: str = "") -> str:
    if date_column:
        col_map = _column_map(db)
        if date_column in col_map and col_map[date_column].get("type") in ("date", "datetime"):
            return date_column
    for c in db.get("columns") or []:
        if c.get("type") in ("date", "datetime"):
            return c["id"]
    return ""


def add_view(
    db_id: str,
    name: str,
    view_type: str = "table",
    *,
    group_column: str = "",
    cover_column: str = "",
    date_column: str = "",
) -> dict[str, Any]:
    db = load_database(db_id)
    view_type = (view_type or "table").strip().lower()
    if view_type not in VIEW_TYPES:
        raise ValueError(f"Unsupported view type: {view_type}")
    view: dict[str, Any] = {
        "id": _new_id("v"),
        "name": (name or "新视图").strip() or "新视图",
        "type": view_type,
        "filters": [],
        "sorts": [],
    }
    columns = db.get("columns") or []
    if view_type == "table":
        view.update({
            "columnWidths": {},
            "hiddenColumns": [],
            "columnOrder": [],
            "groupBy": "",
            "frozenColumns": 1,
            "conditionalFormats": [],
        })
    if view_type == "kanban":
        gid = _pick_kanban_column(db, group_column)
        view["groupColumn"] = gid
        view["kanbanOrder"] = {}
        if not name or name in ("看板", "新视图", "看板视图"):
            col_map = _column_map(db)
            col_name = col_map.get(gid, {}).get("name") or "字段"
            view["name"] = f"看板 · {col_name}"
    if view_type == "gallery":
        title_id = columns[0]["id"] if columns else ""
        view["coverColumn"] = cover_column or title_id
        view["cardFields"] = [c["id"] for c in columns[1:4] if c.get("id")]
        if not name or name in ("画廊", "新视图", "画廊视图"):
            view["name"] = "画廊"
    if view_type == "calendar":
        view["dateColumn"] = _pick_date_column(db, date_column)
        if not name or name in ("日历", "新视图"):
            view["name"] = "日历"
    if view_type == "form":
        view["formFields"] = [
            c["id"] for c in columns if c.get("type") not in READONLY_FIELD_TYPES
        ]
        if not name or name in ("表单", "新视图"):
            view["name"] = "表单"
    db.setdefault("views", []).append(view)
    save_database(db)
    return view


def update_view(db_id: str, view_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    db = load_database(db_id)
    view_id = (view_id or "").strip()
    if not _VIEW_ID_RE.match(view_id):
        raise ValueError("Invalid view id")
    col_map = _column_map(db)
    scalar_keys = (
        "name", "groupColumn", "coverColumn", "dateColumn", "groupBy", "frozenColumns",
    )
    list_keys = ("filters", "sorts", "cardFields", "hiddenColumns", "columnOrder", "conditionalFormats")
    dict_keys = ("columnWidths", "kanbanOrder")
    for view in db.get("views") or []:
        if view.get("id") != view_id:
            continue
        for key in scalar_keys:
            if key in fields and fields[key] is not None:
                if key in ("groupColumn", "coverColumn", "dateColumn", "groupBy"):
                    val = str(fields.get(key) or "")
                    if val and val not in col_map and key != "groupBy":
                        continue
                    view[key] = val
                elif key == "frozenColumns":
                    try:
                        view[key] = int(fields[key])
                    except (TypeError, ValueError):
                        pass
                elif key == "name" and fields[key]:
                    view[key] = str(fields[key]).strip()
                else:
                    view[key] = fields[key]
        for key in list_keys:
            if key in fields and isinstance(fields[key], list):
                if key == "cardFields":
                    view[key] = [str(x) for x in fields[key] if str(x) in col_map]
                else:
                    view[key] = fields[key]
        for key in dict_keys:
            if key in fields and isinstance(fields[key], dict):
                view[key] = fields[key]
        if "formFields" in fields and isinstance(fields["formFields"], list):
            view["formFields"] = [str(x) for x in fields["formFields"] if str(x) in col_map]
        save_database(db)
        return view
    raise ValueError("View not found")


def delete_view(db_id: str, view_id: str) -> None:
    db = load_database(db_id)
    views = db.get("views") or []
    if len(views) <= 1:
        raise ValueError("Cannot delete the last view")
    view_id = (view_id or "").strip()
    if not any(v.get("id") == view_id for v in views):
        raise ValueError("View not found")
    db["views"] = [v for v in views if v.get("id") != view_id]
    if db.get("viewID") == view_id:
        db["viewID"] = db["views"][0]["id"]
    save_database(db)


def add_row(db_id: str, cells: dict[str, Any] | None = None) -> dict[str, Any]:
    db = load_database(db_id)
    cols = _column_map(db)
    now = _now_iso()
    row_cells: dict[str, Any] = {}
    for cid, col in cols.items():
        ctype = col.get("type") or "text"
        if cells and cid in cells:
            row_cells[cid] = _normalize_cell(col, cells[cid])
        elif ctype == "autonumber":
            row_cells[cid] = _next_autonumber(db, cid)
        else:
            row_cells[cid] = _default_cell_value(ctype)
    row = {
        "id": _new_id("r"),
        "cells": row_cells,
        "meta": {"created_at": now, "updated_at": now},
    }
    db.setdefault("rows", []).append(row)
    save_database(db)
    return row


def update_row(db_id: str, row_id: str, cells: dict[str, Any]) -> dict[str, Any]:
    db = load_database(db_id)
    row_id = (row_id or "").strip()
    if not _ROW_ID_RE.match(row_id):
        raise ValueError("Invalid row id")
    cols = _column_map(db)
    for row in db.get("rows") or []:
        if row.get("id") != row_id:
            continue
        merged = dict(row.get("cells") or {})
        for cid, val in (cells or {}).items():
            if cid not in cols:
                continue
            col = cols[cid]
            if col.get("type") in READONLY_FIELD_TYPES and col.get("type") not in ("autonumber",):
                continue
            old_refs = list(merged.get(cid) or []) if col.get("type") == "link" else []
            merged[cid] = _normalize_cell(col, val)
            if col.get("type") == "link":
                new_refs = list(merged.get(cid) or [])
                _sync_bidirectional_links(db, row_id, cid, old_refs, new_refs)
        row["cells"] = merged
        row.setdefault("meta", {})["updated_at"] = _now_iso()
        save_database(db)
        return row
    raise ValueError("Row not found")


def delete_row(db_id: str, row_id: str) -> None:
    db = load_database(db_id)
    row_id = (row_id or "").strip()
    before = len(db.get("rows") or [])
    db["rows"] = [r for r in db.get("rows") or [] if r.get("id") != row_id]
    if len(db["rows"]) == before:
        raise ValueError("Row not found")
    save_database(db)


def add_column(db_id: str, name: str, col_type: str = "text", **extra: Any) -> dict[str, Any]:
    db = load_database(db_id)
    col_type = (col_type or "text").strip().lower()
    if col_type not in FIELD_TYPES:
        raise ValueError(f"Unsupported column type: {col_type}")
    col = _column_defaults(col_type, (name or "新列").strip() or "新列")
    for key, val in extra.items():
        if val is not None:
            col[key] = val
    db.setdefault("columns", []).append(col)
    default_val = _default_cell_value(col_type)
    for row in db.get("rows") or []:
        if col_type == "autonumber":
            row.setdefault("cells", {})[col["id"]] = _next_autonumber(db, col["id"])
        else:
            row.setdefault("cells", {})[col["id"]] = deepcopy(default_val) if isinstance(default_val, list) else default_val
    save_database(db)
    return col


def update_column(db_id: str, col_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    db = load_database(db_id)
    col_id = (col_id or "").strip()
    if not _COL_ID_RE.match(col_id):
        raise ValueError("Invalid column id")
    for col in db.get("columns") or []:
        if col.get("id") != col_id:
            continue
        if "name" in fields and fields["name"]:
            col["name"] = str(fields["name"]).strip()
        if "type" in fields and fields["type"] in FIELD_TYPES:
            col["type"] = fields["type"]
        if "width" in fields:
            try:
                col["width"] = int(fields["width"])
            except (TypeError, ValueError):
                pass
        if "options" in fields and isinstance(fields["options"], list):
            col["options"] = fields["options"]
        if "max" in fields:
            try:
                col["max"] = int(fields["max"])
            except (TypeError, ValueError):
                pass
        for extra in (
            "linkDatabase", "bidirectional", "reverseColumn", "linkColumn",
            "lookupColumn", "rollupColumn", "rollupFn", "expression", "aiPrompt",
        ):
            if extra in fields:
                col[extra] = fields[extra]
        save_database(db)
        return col
    raise ValueError("Column not found")


def delete_column(db_id: str, col_id: str) -> None:
    db = load_database(db_id)
    col_id = (col_id or "").strip()
    cols = db.get("columns") or []
    if len(cols) <= 1:
        raise ValueError("Cannot delete the last column")
    if not any(c.get("id") == col_id for c in cols):
        raise ValueError("Column not found")
    db["columns"] = [c for c in cols if c.get("id") != col_id]
    for row in db.get("rows") or []:
        cells = row.get("cells") or {}
        cells.pop(col_id, None)
    for view in db.get("views") or []:
        if view.get("groupColumn") == col_id:
            view["groupColumn"] = _pick_kanban_column(db, "")
        if view.get("coverColumn") == col_id:
            columns = db.get("columns") or []
            view["coverColumn"] = columns[0]["id"] if columns else ""
        if view.get("dateColumn") == col_id:
            view["dateColumn"] = _pick_date_column(db, "")
        if view.get("groupBy") == col_id:
            view["groupBy"] = ""
        for key in ("hiddenColumns", "columnOrder", "cardFields", "formFields"):
            if isinstance(view.get(key), list):
                view[key] = [x for x in view[key] if x != col_id]
    save_database(db)


def update_database_meta(db_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    db = load_database(db_id)
    if "name" in fields and fields["name"]:
        db["name"] = str(fields["name"]).strip()
    if "icon" in fields and fields["icon"]:
        db["icon"] = str(fields["icon"]).strip()[:8]
    if "description" in fields:
        db["description"] = str(fields.get("description") or "")[:500]
    if "viewID" in fields and fields["viewID"]:
        db["viewID"] = str(fields["viewID"]).strip()
    return save_database(db)


def save_database_attachment(db_id: str, filename: str, content: bytes) -> dict[str, str]:
    safe = re.sub(r"[^\w.\-]", "_", Path(filename).name)[:120] or "file"
    dest_dir = database_attachments_dir(db_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / safe
    dest.write_bytes(content)
    url = f"/.kbase/database_attachments/{validate_database_id(db_id)}/{safe}"
    return {"name": safe, "url": url}


def _clamp_number(val: Any, lo: float, hi: float) -> float | None:
    try:
        num = float(val)
    except (TypeError, ValueError):
        return None
    return max(lo, min(hi, num))


def _normalize_cell(col: dict[str, Any], val: Any) -> Any:
    ctype = col.get("type") or "text"
    if ctype == "checkbox":
        if isinstance(val, bool):
            return val
        return str(val).lower() in ("1", "true", "yes", "on")
    if ctype in ("number", "currency"):
        if val in ("", None):
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None
    if ctype == "percent":
        return _clamp_number(val, 0, 100)
    if ctype == "progress":
        hi = float(col.get("max") or 100)
        return _clamp_number(val, 0, hi)
    if ctype == "rating":
        hi = float(col.get("max") or 5)
        n = _clamp_number(val, 0, hi)
        return int(n) if n is not None else None
    if ctype in ("mselect", "link"):
        if isinstance(val, list):
            return [str(x) for x in val if str(x).strip()]
        if val in ("", None):
            return []
        return [str(val)]
    if ctype == "attachment":
        if isinstance(val, list):
            out = []
            for item in val:
                if isinstance(item, dict):
                    out.append({
                        "name": str(item.get("name") or ""),
                        "url": str(item.get("url") or ""),
                    })
                elif item:
                    out.append({"name": str(item), "url": str(item)})
            return out
        if val in ("", None):
            return []
        return [{"name": str(val), "url": str(val)}]
    if ctype == "autonumber":
        try:
            return int(val)
        except (TypeError, ValueError):
            return None
    if val is None:
        return ""
    return str(val)

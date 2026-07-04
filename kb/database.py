"""Feishu Bitable-inspired databases for KBase (standalone + note blocks)."""
from __future__ import annotations

import re
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from storage import KBASE_DIR, _atomic_write_json

DATABASES_DIR: Path = KBASE_DIR / "databases"
_DB_ID_RE = re.compile(r"^db_[a-zA-Z0-9_-]{4,64}$")
_ROW_ID_RE = re.compile(r"^r_[a-zA-Z0-9_-]{4,64}$")
_COL_ID_RE = re.compile(r"^c_[a-zA-Z0-9_-]{4,64}$")
_VIEW_ID_RE = re.compile(r"^v_[a-zA-Z0-9_-]{4,64}$")

FIELD_TYPES = frozenset({
    "text", "longtext", "number", "currency", "percent", "progress", "rating",
    "date", "datetime", "select", "mselect", "checkbox", "url", "email", "phone",
    "person", "attachment",
})

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
}

VIEW_TYPES = frozenset({"table", "kanban", "gallery"})


def public_field_types() -> list[dict[str, str]]:
    return [
        {"id": t, "label": FIELD_TYPE_LABELS.get(t, t), "kanbanGroup": t in KANBAN_GROUP_TYPES}
        for t in sorted(FIELD_TYPES)
    ]


def validate_database_id(value: str) -> str:
    db_id = (value or "").strip()
    if not _DB_ID_RE.match(db_id):
        raise ValueError("Invalid database id")
    return db_id


def _ensure_dir() -> None:
    DATABASES_DIR.mkdir(parents=True, exist_ok=True)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _db_path(db_id: str) -> Path:
    return DATABASES_DIR / f"{validate_database_id(db_id)}.json"


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _select_options(names: list[str]) -> list[dict[str, str]]:
    colors = ("1", "2", "3", "4", "5", "6", "7", "8")
    return [{"name": n, "color": colors[i % len(colors)]} for i, n in enumerate(names)]


def _default_cell_value(col_type: str) -> Any:
    if col_type == "checkbox":
        return False
    if col_type == "mselect":
        return []
    if col_type == "attachment":
        return []
    if col_type in ("number", "currency", "percent", "progress", "rating"):
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

    columns = [
        title_col, status_col, priority_col, owner_col, due_col,
        progress_col, tags_col, rating_col, note_col, link_col,
    ]
    table_view_id = _new_id("v")
    return {
        "spec": 2,
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
            },
            {
                "id": _new_id("v"),
                "name": "看板 · 状态",
                "type": "kanban",
                "groupColumn": status_col["id"],
                "filters": [],
                "sorts": [],
            },
            {
                "id": _new_id("v"),
                "name": "看板 · 优先级",
                "type": "kanban",
                "groupColumn": priority_col["id"],
                "filters": [],
                "sorts": [],
            },
            {
                "id": _new_id("v"),
                "name": "看板 · 负责人",
                "type": "kanban",
                "groupColumn": owner_col["id"],
                "filters": [],
                "sorts": [],
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
        ],
        "viewID": table_view_id,
    }


def list_databases() -> list[dict[str, Any]]:
    _ensure_dir()
    out: list[dict[str, Any]] = []
    for path in sorted(DATABASES_DIR.glob("db_*.json")):
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
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("id") != db_id:
        raise ValueError("Corrupt database file")
    return data


def save_database(data: dict[str, Any]) -> dict[str, Any]:
    db_id = validate_database_id(str(data.get("id") or ""))
    _ensure_dir()
    payload = deepcopy(data)
    payload["id"] = db_id
    payload["updated_at"] = _now_iso()
    _atomic_write_json(_db_path(db_id), payload)
    return payload


def create_database(name: str = "Untitled") -> dict[str, Any]:
    db_id = _new_id("db")
    data = _default_database(db_id, name)
    return save_database(data)


def delete_database(db_id: str) -> None:
    path = _db_path(db_id)
    if path.exists():
        path.unlink()


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
            keys.append(str(val).lower() if spec.get("order", "asc") == "asc" else "")
        return tuple(keys)

    try:
        out.sort(key=sort_key)
    except Exception:
        pass
    return out


def _apply_filters(rows: list[dict[str, Any]], filters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not filters:
        return rows
    out: list[dict[str, Any]] = []
    for row in rows:
        cells = row.get("cells") or {}
        keep = True
        for f in filters:
            col = f.get("column") or f.get("id")
            op = f.get("op") or "contains"
            want = f.get("value", "")
            got = cells.get(col, "")
            if isinstance(got, list):
                got = ",".join(str(x) for x in got)
            got_s = str(got)
            want_s = str(want)
            if op == "eq" and got_s != want_s:
                keep = False
                break
            if op == "contains" and want_s.lower() not in got_s.lower():
                keep = False
                break
            if op == "empty" and got_s.strip():
                keep = False
                break
        if keep:
            out.append(row)
    return out


def _kanban_column_labels(col_def: dict[str, Any] | None) -> list[str]:
    if not col_def:
        return []
    ctype = col_def.get("type") or ""
    if ctype in ("select", "mselect"):
        return [str(o.get("name") or "") for o in col_def.get("options") or [] if o.get("name")]
    return []


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
        val = (row.get("cells") or {}).get(group_col_id, "")
        keys: list[str] = []
        if ctype == "mselect" and isinstance(val, list):
            keys = [str(x) for x in val if str(x).strip()]
        elif val not in ("", None, False):
            keys = [str(val)]
        if not keys:
            buckets["__ungrouped__"].append(row)
            continue
        for key in keys:
            if key not in buckets:
                buckets[key] = []
            buckets[key].append(row)

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
            return str(first.get("url") or first.get("name") or "")
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
        cells = row.get("cells") or {}
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
            "row": row,
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


def render_database(db_id: str, view_id: str | None = None) -> dict[str, Any]:
    db = load_database(db_id)
    view = _active_view(db, view_id)
    rows = _apply_filters(list(db.get("rows") or []), view.get("filters") or [])
    rows = _apply_sorts(rows, view.get("sorts") or [])
    columns = db.get("columns") or []
    result: dict[str, Any] = {
        "id": db["id"],
        "name": db.get("name") or "Untitled",
        "icon": db.get("icon") or "📊",
        "description": db.get("description") or "",
        "view": view,
        "views": db.get("views") or [],
        "columns": columns,
        "rows": rows,
        "fieldTypes": public_field_types(),
    }
    vtype = view.get("type") or "table"
    if vtype == "kanban":
        result["kanban"] = _render_kanban_groups(db, view, rows)
    elif vtype == "gallery":
        result["gallery"] = _render_gallery(db, view, rows)
    return result


def _pick_kanban_column(db: dict[str, Any], group_column: str = "") -> str:
    if group_column:
        col_map = _column_map(db)
        if group_column in col_map and col_map[group_column].get("type") in KANBAN_GROUP_TYPES:
            return group_column
    for c in db.get("columns") or []:
        if c.get("type") in KANBAN_GROUP_TYPES:
            return c["id"]
    return ""


def add_view(
    db_id: str,
    name: str,
    view_type: str = "table",
    *,
    group_column: str = "",
    cover_column: str = "",
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
        view["columnWidths"] = {}
    if view_type == "kanban":
        gid = _pick_kanban_column(db, group_column)
        view["groupColumn"] = gid
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
    db.setdefault("views", []).append(view)
    save_database(db)
    return view


def update_view(db_id: str, view_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    db = load_database(db_id)
    view_id = (view_id or "").strip()
    if not _VIEW_ID_RE.match(view_id):
        raise ValueError("Invalid view id")
    col_map = _column_map(db)
    for view in db.get("views") or []:
        if view.get("id") != view_id:
            continue
        if "name" in fields and fields["name"]:
            view["name"] = str(fields["name"]).strip()
        if "filters" in fields and isinstance(fields["filters"], list):
            view["filters"] = fields["filters"]
        if "sorts" in fields and isinstance(fields["sorts"], list):
            view["sorts"] = fields["sorts"]
        if "groupColumn" in fields:
            gid = str(fields.get("groupColumn") or "")
            if gid and gid in col_map:
                view["groupColumn"] = gid
        if "coverColumn" in fields:
            cid = str(fields.get("coverColumn") or "")
            if cid and cid in col_map:
                view["coverColumn"] = cid
        if "cardFields" in fields and isinstance(fields["cardFields"], list):
            view["cardFields"] = [str(x) for x in fields["cardFields"] if str(x) in col_map]
        if "columnWidths" in fields and isinstance(fields["columnWidths"], dict):
            view["columnWidths"] = fields["columnWidths"]
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
    row_cells: dict[str, Any] = {}
    for cid, col in cols.items():
        if cells and cid in cells:
            row_cells[cid] = _normalize_cell(col, cells[cid])
        else:
            row_cells[cid] = _default_cell_value(col.get("type") or "text")
    row = {"id": _new_id("r"), "cells": row_cells}
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
            merged[cid] = _normalize_cell(cols[cid], val)
        row["cells"] = merged
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


def add_column(db_id: str, name: str, col_type: str = "text") -> dict[str, Any]:
    db = load_database(db_id)
    col_type = (col_type or "text").strip().lower()
    if col_type not in FIELD_TYPES:
        raise ValueError(f"Unsupported column type: {col_type}")
    col = _column_defaults(col_type, (name or "新列").strip() or "新列")
    db.setdefault("columns", []).append(col)
    default_val = _default_cell_value(col_type)
    for row in db.get("rows") or []:
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
    if ctype == "mselect":
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
    if val is None:
        return ""
    return str(val)

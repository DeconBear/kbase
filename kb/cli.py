"""KBase CLI — Layer 1 agent interface (workspace-native)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running as ``python kb/cli.py`` from repo root.
_KB_DIR = Path(__file__).resolve().parent
if str(_KB_DIR) not in sys.path:
    sys.path.insert(0, str(_KB_DIR))

from app_config import load_recent_workspaces  # noqa: E402
from storage import DATA_ROOT, ensure_directories, load_local_env  # noqa: E402
from workspace import (  # noqa: E402
    DOC_ID_RE,
    Workspace,
    get_active_workspace,
    open_workspace,
    require_active_workspace,
)


def _resolve_workspace(path: str | None) -> Workspace:
    if path:
        return open_workspace(path, scan=False)
    env = (Path.cwd() / ".kbase").exists()
    if env:
        return open_workspace(Path.cwd(), scan=False)
    active = get_active_workspace()
    if active is not None:
        return active
    default = str(DATA_ROOT)
    if Path(default).is_dir():
        return open_workspace(default, scan=False)
    raise SystemExit("未指定工作空间。使用 --workspace /path/to/ws")


def _emit(data: object, *, as_json: bool) -> None:
    if as_json:
        text = json.dumps(data, ensure_ascii=False, indent=2)
        try:
            print(text)
        except UnicodeEncodeError:
            sys.stdout.buffer.write(text.encode("utf-8", errors="replace") + b"\n")
    elif isinstance(data, dict):
        for key, val in data.items():
            print(f"{key}: {val}")
    elif isinstance(data, list):
        for row in data:
            if isinstance(row, dict):
                print(row.get("id", ""), row.get("path", ""), row.get("title", ""))
            else:
                print(row)
    else:
        print(data)


def cmd_workspace_info(args: argparse.Namespace) -> int:
    ws = _resolve_workspace(args.workspace)
    _emit(ws.info(), as_json=args.json)
    return 0


def cmd_workspace_scan(args: argparse.Namespace) -> int:
    ws = _resolve_workspace(args.workspace)
    stats = ws.scan(full=args.full)
    _emit(stats, as_json=args.json)
    return 0


def cmd_workspace_recent(_args: argparse.Namespace) -> int:
    _emit(load_recent_workspaces(), as_json=_args.json)
    return 0


def cmd_workspace_migrate(args: argparse.Namespace) -> int:
    from migrate_workspace import run_migration

    from_root = Path(args.from_root or DATA_ROOT)
    to_root = Path(args.to_root or from_root)
    report = run_migration(
        from_root,
        to_root,
        dry_run=args.dry_run,
        reindex_only=args.reindex_only,
    )
    _emit(report, as_json=args.json)
    return 0 if report.get("ok") else 1


def cmd_doc_list(args: argparse.Namespace) -> int:
    ws = _resolve_workspace(args.workspace)
    docs = ws.list_documents(kind=args.kind or None, query=args.query or None)
    _emit(docs, as_json=args.json)
    return 0


def cmd_doc_show(args: argparse.Namespace) -> int:
    ws = _resolve_workspace(args.workspace)
    target = args.target
    if DOC_ID_RE.match(target):
        doc = ws.load_document(target)
    else:
        rel = target.replace("\\", "/")
        doc = next(
            (d for d in ws.list_documents() if d.get("path") == rel),
            None,
        )
    if not doc:
        raise SystemExit(f"未找到文档: {target}")
    _emit(doc, as_json=args.json)
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    ws = get_active_workspace()
    _emit(
        {
            "active": ws.info() if ws else None,
            "dataRoot": str(DATA_ROOT),
        },
        as_json=_args.json,
    )
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    from workspace_search import search_workspace_documents

    ws = _resolve_workspace(args.workspace)
    hits = search_workspace_documents(ws, args.query, limit=args.limit)
    _emit(hits, as_json=args.json)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kbase", description="KBase workspace CLI")
    parser.add_argument(
        "--workspace",
        "-w",
        help="工作空间根目录（默认：活动工作空间或 data/）",
    )
    parser.add_argument("--json", action="store_true", help="JSON 输出")

    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="显示活动工作空间")
    p_status.set_defaults(func=cmd_status)

    ws = sub.add_parser("workspace", help="工作空间操作")
    ws_sub = ws.add_subparsers(dest="ws_cmd", required=True)

    p_info = ws_sub.add_parser("info", help="工作空间信息")
    p_info.set_defaults(func=cmd_workspace_info)

    p_scan = ws_sub.add_parser("scan", help="扫描并同步 sidecar")
    p_scan.add_argument("--full", action="store_true", help="全量扫描")
    p_scan.set_defaults(func=cmd_workspace_scan)

    p_recent = ws_sub.add_parser("recent", help="最近打开的工作空间")
    p_recent.set_defaults(func=cmd_workspace_recent)

    p_migrate = ws_sub.add_parser("migrate", help="从 legacy data/ 迁移 sidecar")
    p_migrate.add_argument("--from", dest="from_root", default=None)
    p_migrate.add_argument("--to", dest="to_root", default=None)
    p_migrate.add_argument("--dry-run", action="store_true")
    p_migrate.add_argument("--reindex-only", action="store_true")
    p_migrate.set_defaults(func=cmd_workspace_migrate)

    doc = sub.add_parser("doc", help="文档操作")
    doc_sub = doc.add_subparsers(dest="doc_cmd", required=True)

    p_list = doc_sub.add_parser("list", help="列出文档")
    p_list.add_argument("--kind", help="按 kind 过滤，如 pdf")
    p_list.add_argument("--query", "-q", help="标题/路径搜索")
    p_list.set_defaults(func=cmd_doc_list)

    p_show = doc_sub.add_parser("show", help="查看文档 sidecar")
    p_show.add_argument("target", help="doc_id 或相对路径")
    p_show.set_defaults(func=cmd_doc_show)

    p_search = sub.add_parser("search", help="工作空间全文搜索")
    p_search.add_argument("query", help="搜索词")
    p_search.add_argument("--limit", type=int, default=20)
    p_search.set_defaults(func=cmd_search)

    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_directories()
    load_local_env()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

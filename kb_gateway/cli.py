"""命令行入口：init / kb add / reindex / list / serve。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .indexer import index_kb
from .registry import load_raw, load_registry, registry_file, save_raw
from .tools import kb_list

INIT_TEMPLATE = """version: 1
embedding:
  active_profile: builtin-hash
  profiles:
    - profile_id: builtin-hash
      type: builtin          # 仅离线冒烟；生产改成 online 或 local
      dim: 512
      batch_size: 32
storage:
  lancedb_path: ~/.kb-gateway/data/lance
  sqlite_path: ~/.kb-gateway/data/meta.db
knowledge_bases: []
"""


def _print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.config).expanduser() if args.config else registry_file()
    if target.exists() and not args.force:
        _print({"error": f"注册表已存在：{target}（加 --force 覆盖）"})
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(INIT_TEMPLATE, encoding="utf-8")
    _print({"created": str(target)})
    return 0


def _cmd_kb_add(args: argparse.Namespace) -> int:
    path = Path(args.config).expanduser() if args.config else registry_file()
    if not path.exists():
        _print({"error": f"注册表不存在：{path}"})
        return 1
    data = load_raw(path)
    items = data.setdefault("knowledge_bases", [])
    if any(item.get("kb_id") == args.id for item in items):
        _print({"error": f"kb_id 已存在：{args.id}"})
        return 1
    entry = {
        "kb_id": args.id,
        "display_name": args.display_name or args.id,
        "source_type": args.source_type,
        "repo": args.repo,
        "knowledge_root": args.knowledge_root,
        "index_mode": args.index_mode
        or ("git-object" if args.source_type == "embedded" else "filesystem"),
        "index_ref": args.index_ref,
        "serve_refs": [args.index_ref],
        "default_authority": args.authority,
        "default_write": "proposal_only" if args.source_type == "external" else "no",
        "vector_enabled": not args.no_vector,
        "include": ["**/*.md"],
        "exclude": ["**/.obsidian/**", "**/.trash/**"],
    }
    items.append(entry)
    save_raw(path, data)
    _print({"added": entry, "registry": str(path)})
    return 0


def _cmd_reindex(args: argparse.Namespace) -> int:
    ctx = load_registry(args.config)
    targets = ctx.registry.knowledge_bases if args.all else [ctx.kb(args.kb)]
    if not targets:
        _print({"error": "没有可索引的知识库"})
        return 1
    reports = []
    failed = False
    for kb in targets:
        try:
            reports.append(index_kb(ctx, kb, full=args.full).to_dict())
        except Exception as exc:  # noqa: BLE001 - CLI 需要一次性汇总所有失败
            failed = True
            reports.append({"kb_id": kb.kb_id, "error": str(exc)})
    _print({"reports": reports})
    return 1 if failed else 0


def _cmd_list(args: argparse.Namespace) -> int:
    _print(kb_list(load_registry(args.config), include_prefixes=args.prefixes))
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    if args.config:
        os.environ["KB_CONFIG"] = str(Path(args.config).expanduser())
    from .server import serve

    serve(transport=args.transport, host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kb-gateway", description="MCP 知识库网关（P0）")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config",
        default=argparse.SUPPRESS,
        help="注册表路径，默认 KB_CONFIG 或 ~/.kb-gateway/kb_registry.yaml",
    )
    parser.add_argument("--config", help="注册表路径，默认 KB_CONFIG 或 ~/.kb-gateway/kb_registry.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="生成注册表模板", parents=[common])
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=_cmd_init)

    kb = sub.add_parser("kb", help="知识库管理", parents=[common])
    kb_sub = kb.add_subparsers(dest="action", required=True)
    add = kb_sub.add_parser("add", help="注册知识库", parents=[common])
    add.add_argument("--id", required=True)
    add.add_argument("--repo", required=True)
    add.add_argument("--display-name")
    add.add_argument("--source-type", choices=["embedded", "external"], default="external")
    add.add_argument("--knowledge-root", default="knowledge")
    add.add_argument("--index-mode", choices=["git-object", "filesystem"])
    add.add_argument("--index-ref", default="main")
    add.add_argument("--authority", choices=["contract", "frozen", "note", "draft"], default="note")
    add.add_argument("--no-vector", action="store_true")
    add.set_defaults(func=_cmd_kb_add)

    reindex = sub.add_parser("reindex", help="构建索引", parents=[common])
    target = reindex.add_mutually_exclusive_group(required=True)
    target.add_argument("--kb")
    target.add_argument("--all", action="store_true")
    reindex.add_argument("--full", action="store_true", help="忽略内容哈希，全量重建")
    reindex.set_defaults(func=_cmd_reindex)

    listing = sub.add_parser("list", help="列出知识库与索引状态", parents=[common])
    listing.add_argument("--prefixes", action="store_true")
    listing.set_defaults(func=_cmd_list)

    serve = sub.add_parser("serve", help="启动 MCP 服务", parents=[common])
    serve.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help="stdio 由客户端拉起；streamable-http / sse 为独立 HTTP 服务",
    )
    serve.add_argument("--host", default="127.0.0.1", help="HTTP 监听地址，默认 127.0.0.1")
    serve.add_argument("--port", type=int, default=8787, help="HTTP 监听端口，默认 8787")
    serve.set_defaults(func=_cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "config", None):
        os.environ["KB_CONFIG"] = str(Path(args.config).expanduser())
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001 - 统一转成可读错误
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stdout)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

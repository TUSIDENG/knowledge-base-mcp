"""索引构建：文件 / git 对象采集、解析分块、向量化与增量更新。"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .chunker import Chunk, chunk_markdown, chunk_plain
from .embedder import build_embedder
from .parser import first_heading, parse_markdown
from .registry import KnowledgeBase, RegistryContext
from .store import StateStore, VectorStore

TEXT_SUFFIXES = {".md", ".markdown", ".yaml", ".yml", ".sql", ".txt", ".json"}


@dataclass
class IndexReport:
    kb_id: str
    indexed_ref: str
    added: int = 0
    updated: int = 0
    skipped: int = 0
    deleted: int = 0
    chunks: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "kb_id": self.kb_id,
            "indexed_ref": self.indexed_ref,
            "added": self.added,
            "updated": self.updated,
            "skipped": self.skipped,
            "deleted": self.deleted,
            "chunks": self.chunks,
            "errors": self.errors,
        }


@dataclass
class SourceDoc:
    path: str
    abs_path: str
    text: str
    content_hash: str
    git_ref: str


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _doc_id(kb: KnowledgeBase, path: str) -> str:
    return "sha1:" + hashlib.sha1(f"{kb.kb_id}:{path}".encode("utf-8")).hexdigest()[:16]


def _matches(path: str, patterns: list[str]) -> bool:
    probe = PurePosixPath(path)
    for pattern in patterns:
        if probe.match(pattern) or (pattern.startswith("**/") and probe.match(pattern[3:])):
            return True
    return False


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{completed.stderr.strip()[:300]}")
    return completed.stdout


def _filesystem_docs(kb: KnowledgeBase, ctx: RegistryContext, report: IndexReport) -> tuple[list[SourceDoc], str]:
    repo = ctx.repo_path(kb)
    scan_root = ctx.scan_root(kb)
    if not scan_root.exists():
        raise FileNotFoundError(f"知识库目录不存在：{scan_root}")
    docs: list[SourceDoc] = []
    for file in sorted(scan_root.rglob("*")):
        if not file.is_file():
            continue
        scan_rel = file.relative_to(scan_root).as_posix()
        repo_rel = file.resolve().relative_to(repo).as_posix()
        if not _matches(scan_rel, kb.include) or _matches(scan_rel, kb.exclude):
            continue
        if file.suffix.lower() not in TEXT_SUFFIXES:
            report.errors.append(f"跳过不支持的文件类型：{repo_rel}")
            continue
        text = file.read_text(encoding="utf-8", errors="replace")
        docs.append(
            SourceDoc(
                path=repo_rel,
                abs_path=str(file.resolve()),
                text=text,
                content_hash=_sha256(text),
                git_ref="worktree",
            )
        )
    return docs, "worktree"


def _git_docs(kb: KnowledgeBase, ctx: RegistryContext, report: IndexReport) -> tuple[list[SourceDoc], str]:
    repo = ctx.repo_path(kb)
    commit = _git(repo, "rev-parse", f"{kb.index_ref}^{{commit}}").strip()
    root = kb.knowledge_root.strip("./")
    paths = _git(
        repo, "-c", "core.quotepath=false", "ls-tree", "-r", "-z", "--name-only", commit, "--", root or "."
    ).split("\0")
    scan_root = ctx.scan_root(kb)
    docs: list[SourceDoc] = []
    for repo_rel in paths:
        file = Path(repo_rel)
        if file.suffix.lower() not in TEXT_SUFFIXES:
            continue
        scan_rel = PurePosixPath(repo_rel).relative_to(root).as_posix() if root else repo_rel
        if not _matches(scan_rel, kb.include) or _matches(scan_rel, kb.exclude):
            continue
        text = _git(repo, "show", f"{commit}:{repo_rel}")
        docs.append(
            SourceDoc(
                path=repo_rel,
                abs_path=str((scan_root / scan_rel).resolve()),
                text=text,
                content_hash=_sha256(text),
                git_ref=f"{kb.index_ref}@{commit[:12]}",
            )
        )
    return docs, f"{kb.index_ref}@{commit[:12]}"


def _as_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str) and value:
        return [value]
    return []


def _doc_type(partition: str) -> str:
    return {
        "spec": "spec",
        "adr": "adr",
        "api": "api",
        "guides": "guide",
        "reports": "report",
    }.get(partition, "doc")


def _build_rows(
    kb: KnowledgeBase,
    ctx: RegistryContext,
    doc: SourceDoc,
    chunks: list[Chunk],
    vectors: list[list[float]],
) -> list[dict]:
    front, body = {}, doc.text
    if doc.path.lower().endswith((".md", ".markdown")):
        parsed = parse_markdown(doc.text)
        front, body = parsed.front_matter, parsed.body

    partition = ctx.partition_of(kb, doc.path)
    root = kb.knowledge_root.strip("./")
    rel_path = doc.path[len(root) + 1:] if root and doc.path.startswith(root + "/") else doc.path
    rule = ctx.rule_for(kb, doc.path)
    authority = (rule.authority if rule else None) or front.get("authority") or kb.default_authority
    title = str(front.get("title") or first_heading(body) or Path(doc.path).stem)
    doc_id = _doc_id(kb, doc.path)

    rows: list[dict] = []
    for chunk, vector in zip(chunks, vectors):
        heading = chunk.heading_path or title
        enriched = "\n".join(
            part
            for part in [
                f"# {heading}" if heading else "",
                " | ".join(chunk.table_header) if chunk.table_header else "",
                chunk.content,
            ]
            if part
        )
        rows.append(
            {
                "kb_id": kb.kb_id,
                "doc_id": doc_id,
                "path": doc.path,
                "rel_path": rel_path,
                "abs_path": doc.abs_path,
                "partition": partition,
                "chunk_index": chunk.index,
                "kind": chunk.kind,
                "heading_path": heading,
                "content": chunk.content,
                "enriched": enriched,
                "doc_type": str(front.get("doc_type") or _doc_type(partition)),
                "status": str(front.get("status") or "active"),
                "domain": ", ".join(_as_list(front.get("domain"))),
                "systems": ", ".join(_as_list(front.get("systems"))),
                "version": str(front.get("version") or ""),
                "authority": authority,
                "authoritative": authority == "contract",
                "effective_from": str(front.get("effective_from") or ""),
                "git_ref": doc.git_ref,
                "content_hash": doc.content_hash,
                "vector": vector,
            }
        )
    return rows


def _chunks_for(doc: SourceDoc) -> list[Chunk]:
    if doc.path.lower().endswith((".md", ".markdown")):
        return chunk_markdown(parse_markdown(doc.text).body)
    return chunk_plain(doc.text)


def read_source(ctx: RegistryContext, kb: KnowledgeBase, path: str) -> tuple[str, str]:
    """返回 (正文, git_ref)；git-object 模式读取已索引的 ref，保证与索引一致。"""
    if kb.index_mode == "git-object":
        repo = ctx.repo_path(kb)
        commit = _git(repo, "rev-parse", f"{kb.index_ref}^{{commit}}").strip()
        return _git(repo, "show", f"{commit}:{path}"), f"{kb.index_ref}@{commit[:12]}"
    file = ctx.repo_path(kb) / PurePosixPath(path)
    return file.read_text(encoding="utf-8", errors="replace"), "worktree"


def index_kb(ctx: RegistryContext, kb: KnowledgeBase, full: bool = False) -> IndexReport:
    ctx.registry.require_storage()
    ctx.registry.embedding.active()
    profile = ctx.registry.embedding.active()
    state = StateStore(ctx.registry.storage.sqlite_path)
    report = IndexReport(kb_id=kb.kb_id, indexed_ref=kb.index_ref)

    try:
        if kb.index_mode == "git-object":
            docs, rev = _git_docs(kb, ctx, report)
        else:
            docs, rev = _filesystem_docs(kb, ctx, report)
        report.indexed_ref = rev

        previous = state.docs(kb.kb_id)
        current = {doc.path: doc for doc in docs}
        changed = [
            doc
            for doc in docs
            if full or doc.path not in previous or previous[doc.path]["content_hash"] != doc.content_hash
        ]
        removed = [path for path in previous if path not in current]

        store = VectorStore(ctx.registry.storage.lancedb_path, kb.kb_id, profile.dim)
        previous_state = state.kb_state(kb.kb_id)
        if previous_state and previous_state["vector_dim"] not in (None, profile.dim):
            if not full:
                raise RuntimeError(
                    f"{kb.kb_id} 向量维度已变化（{previous_state['vector_dim']} → {profile.dim}），请执行 reindex --full"
                )
            store.reset()

        pending = [(doc, _chunks_for(doc)) for doc in changed]
        pending = [(doc, chunks) for doc, chunks in pending if chunks]
        if pending:
            embedder = build_embedder(profile, ctx.registry.storage.model_cache_path)
            texts = [chunk.content for _, chunks in pending for chunk in chunks]
            vectors = embedder.embed(texts)
            cursor = 0
            for doc, chunks in pending:
                rows = _build_rows(kb, ctx, doc, chunks, vectors[cursor:cursor + len(chunks)])
                cursor += len(chunks)
                store.upsert_doc(rows)
                state.put_doc(
                    kb.kb_id,
                    doc.path,
                    _doc_id(kb, doc.path),
                    ctx.partition_of(kb, doc.path),
                    doc.content_hash,
                    doc.git_ref,
                    len(rows),
                )
                report.chunks += len(rows)
                if doc.path in previous:
                    report.updated += 1
                else:
                    report.added += 1

        if removed:
            store.delete_docs([previous[path]["doc_id"] for path in removed])
            state.drop_docs(kb.kb_id, removed)
            report.deleted = len(removed)
        report.skipped = len(docs) - len(changed)

        rows_now = state.docs(kb.kb_id)
        state.set_kb_state(
            kb.kb_id,
            rev,
            len(rows_now),
            sum(row["chunk_count"] for row in rows_now.values()),
            profile.dim,
        )
        state.commit()
        return report
    finally:
        state.close()

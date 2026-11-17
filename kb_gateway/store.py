"""LanceDB 向量存储与 SQLite 索引状态。"""

from __future__ import annotations

import sqlite3
import warnings
from datetime import datetime, timezone
from pathlib import Path

import lancedb
import pyarrow as pa

_FIELDS: list[tuple[str, pa.DataType]] = [
    ("kb_id", pa.string()),
    ("doc_id", pa.string()),
    ("path", pa.string()),
    ("rel_path", pa.string()),
    ("abs_path", pa.string()),
    ("partition", pa.string()),
    ("chunk_index", pa.int32()),
    ("kind", pa.string()),
    ("heading_path", pa.string()),
    ("content", pa.string()),
    ("enriched", pa.string()),
    ("doc_type", pa.string()),
    ("status", pa.string()),
    ("domain", pa.string()),
    ("systems", pa.string()),
    ("version", pa.string()),
    ("authority", pa.string()),
    ("authoritative", pa.bool_()),
    ("effective_from", pa.string()),
    ("git_ref", pa.string()),
    ("content_hash", pa.string()),
]


def _schema(dim: int) -> pa.Schema:
    return pa.schema([*_FIELDS, ("vector", pa.list_(pa.float32(), dim))])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class VectorStore:
    def __init__(self, path: str, kb_id: str, dim: int):
        Path(path).mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(path)
        self._name = f"kb_{kb_id.replace('-', '_')}"
        self.dim = dim
        self._table = self._open_or_create(dim, overwrite=False)

    def _names(self) -> list[str]:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            return list(self._db.table_names())

    def _open_or_create(self, dim: int, overwrite: bool):
        if overwrite or self._name not in self._names():
            if not overwrite and self._name in self._names():
                return self._db.open_table(self._name)
            return self._db.create_table(self._name, schema=_schema(dim), mode="overwrite")
        return self._db.open_table(self._name)

    def reset(self) -> None:
        self._table = self._open_or_create(self.dim, overwrite=True)

    def upsert_doc(self, rows: list[dict]) -> None:
        if not rows:
            return
        doc_id = rows[0]["doc_id"]
        self._table.delete(f"doc_id = '{doc_id}'")
        self._table.add(rows)

    def delete_docs(self, doc_ids: list[str]) -> None:
        if not doc_ids:
            return
        joined = "', '".join(doc_id.replace("'", "") for doc_id in doc_ids)
        self._table.delete(f"doc_id IN ('{joined}')")

    def count(self) -> int:
        return self._table.count_rows()

    def search(self, vector: list[float], where: str | None, limit: int) -> list[dict]:
        query = self._table.search(vector).metric("cosine")
        if where:
            query = query.where(where, prefilter=True)
        return query.limit(limit).to_list()


class StateStore:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS kb_state (
                kb_id TEXT PRIMARY KEY,
                last_indexed TEXT,
                indexed_rev TEXT,
                doc_count INTEGER DEFAULT 0,
                chunk_count INTEGER DEFAULT 0,
                vector_dim INTEGER
            );
            CREATE TABLE IF NOT EXISTS docs (
                kb_id TEXT NOT NULL,
                path TEXT NOT NULL,
                doc_id TEXT NOT NULL,
                partition TEXT NOT NULL DEFAULT '',
                content_hash TEXT NOT NULL,
                git_ref TEXT NOT NULL DEFAULT '',
                chunk_count INTEGER NOT NULL DEFAULT 0,
                indexed_at TEXT NOT NULL,
                PRIMARY KEY (kb_id, path)
            );
            """
        )
        self._conn.commit()

    def docs(self, kb_id: str) -> dict[str, sqlite3.Row]:
        rows = self._conn.execute("SELECT * FROM docs WHERE kb_id = ?", (kb_id,)).fetchall()
        return {row["path"]: row for row in rows}

    def put_doc(
        self,
        kb_id: str,
        path: str,
        doc_id: str,
        partition: str,
        content_hash: str,
        git_ref: str,
        chunk_count: int,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO docs (kb_id, path, doc_id, partition, content_hash, git_ref, chunk_count, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(kb_id, path) DO UPDATE SET
                doc_id = excluded.doc_id,
                partition = excluded.partition,
                content_hash = excluded.content_hash,
                git_ref = excluded.git_ref,
                chunk_count = excluded.chunk_count,
                indexed_at = excluded.indexed_at
            """,
            (kb_id, path, doc_id, partition, content_hash, git_ref, chunk_count, _now()),
        )

    def drop_docs(self, kb_id: str, paths: list[str]) -> None:
        self._conn.executemany("DELETE FROM docs WHERE kb_id = ? AND path = ?", [(kb_id, path) for path in paths])

    def set_kb_state(
        self, kb_id: str, rev: str, doc_count: int, chunk_count: int, vector_dim: int
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO kb_state (kb_id, last_indexed, indexed_rev, doc_count, chunk_count, vector_dim)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(kb_id) DO UPDATE SET
                last_indexed = excluded.last_indexed,
                indexed_rev = excluded.indexed_rev,
                doc_count = excluded.doc_count,
                chunk_count = excluded.chunk_count,
                vector_dim = excluded.vector_dim
            """,
            (kb_id, _now(), rev, doc_count, chunk_count, vector_dim),
        )

    def kb_state(self, kb_id: str) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM kb_state WHERE kb_id = ?", (kb_id,)).fetchone()

    def get_doc(self, kb_id: str, path: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM docs WHERE kb_id = ? AND path = ?", (kb_id, path)
        ).fetchone()

    def find_doc(self, doc_id: str) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM docs WHERE doc_id = ? LIMIT 1", (doc_id,)).fetchone()

    def partitions(self, kb_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT partition FROM docs WHERE kb_id = ? ORDER BY partition", (kb_id,)
        ).fetchall()
        return [row["partition"] for row in rows if row["partition"]]

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

"""向量模型路由：builtin（离线占位）、online（OpenAI 兼容）、local（按需安装）。"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Protocol

import httpx

from .registry import EmbeddingProfile

_WORD_RE = re.compile(r"[A-Za-z0-9_./\-]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _tokenize(text: str) -> list[str]:
    tokens = [token.lower() for token in _WORD_RE.findall(text)]
    for run in _CJK_RE.findall(text):
        tokens.extend(run)
        tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


class HashEmbedder:
    """无依赖的确定性特征哈希，仅用于离线冒烟与测试，检索质量有限。"""

    def __init__(self, dim: int):
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dim
            for token in _tokenize(text):
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
                value = int.from_bytes(digest, "big")
                index = value % self.dim
                vector[index] += 1.0 if (value >> 63) & 1 else -1.0
            norm = sum(item * item for item in vector) ** 0.5 or 1.0
            vectors.append([item / norm for item in vector])
        return vectors


class OpenAICompatibleEmbedder:
    def __init__(self, profile: EmbeddingProfile, api_key: str):
        self.dim = profile.dim
        self.model = profile.model or ""
        self.endpoint = (profile.endpoint or "").rstrip("/")
        self.batch_size = profile.batch_size
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        with httpx.Client(timeout=60.0) as client:
            for start in range(0, len(texts), self.batch_size):
                batch = texts[start:start + self.batch_size]
                response = client.post(
                    f"{self.endpoint}/embeddings",
                    headers=self._headers,
                    json={"model": self.model, "input": batch},
                )
                if response.status_code >= 400:
                    raise RuntimeError(f"向量接口返回 {response.status_code}：{response.text[:300]}")
                payload = response.json()
                rows = sorted(payload.get("data", []), key=lambda row: row.get("index", 0))
                if len(rows) != len(batch):
                    raise RuntimeError(f"向量条数不匹配：请求 {len(batch)}，返回 {len(rows)}")
                for row in rows:
                    vector = [float(item) for item in row["embedding"]]
                    if len(vector) != self.dim:
                        raise ValueError(
                            f"模型维度不匹配：profile.dim={self.dim}，接口返回 {len(vector)}"
                        )
                    vectors.append(vector)
        return vectors


class LocalEmbedder:
    """本地模型：延迟导入 sentence-transformers，未安装时给出安装提示。"""

    def __init__(self, profile: EmbeddingProfile, cache_dir: str):
        self.dim = profile.dim
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - 依赖可选安装
            raise RuntimeError(
                "本地向量模型需要额外依赖：uv sync --extra local-models（或在 Web 管理面启动本地模型时自动安装）"
            ) from exc
        kwargs = {"cache_folder": cache_dir} if cache_dir else {}
        if profile.revision:
            kwargs["revision"] = profile.revision
        self._model = SentenceTransformer(profile.model or "", **kwargs)

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(texts, normalize_embeddings=True, batch_size=16)
        result = [[float(value) for value in vector] for vector in vectors]
        for vector in result:
            if len(vector) != self.dim:
                raise ValueError(f"模型维度不匹配：profile.dim={self.dim}，模型输出 {len(vector)}")
        return result


def _secret(secret_ref: str) -> str:
    if secret_ref.startswith("env:"):
        name = secret_ref[4:]
        value = os.environ.get(name)
        if not value:
            raise RuntimeError(f"环境变量 {name} 未设置（secret_ref={secret_ref}）")
        return value
    if secret_ref.startswith("file:"):
        return Path(secret_ref[5:]).expanduser().read_text(encoding="utf-8").strip()
    return secret_ref


def build_embedder(profile: EmbeddingProfile, model_cache_path: str = "") -> Embedder:
    if profile.type == "builtin":
        return HashEmbedder(profile.dim)
    if profile.type == "online":
        return OpenAICompatibleEmbedder(profile, _secret(profile.secret_ref or ""))
    return LocalEmbedder(profile, model_cache_path)

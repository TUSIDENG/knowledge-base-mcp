"""kb_registry.yaml 的解析与校验。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

SourceType = Literal["external", "embedded"]
IndexMode = Literal["git-object", "filesystem"]
Authority = Literal["contract", "frozen", "note", "draft"]

DEFAULT_REGISTRY = Path.home() / ".kb-gateway" / "kb_registry.yaml"


class EmbeddingProfile(BaseModel):
    profile_id: str
    type: Literal["online", "local", "builtin"] = "online"
    dim: int
    provider: str | None = None
    endpoint: str | None = None
    model: str | None = None
    secret_ref: str | None = None
    revision: str | None = None
    batch_size: int = Field(default=16, ge=1, le=256)

    @model_validator(mode="after")
    def _check_fields(self) -> "EmbeddingProfile":
        if self.type == "online":
            if not self.endpoint or not self.model:
                raise ValueError(f"profile {self.profile_id}: online 类型需要 endpoint 与 model")
            if not self.secret_ref:
                raise ValueError(f"profile {self.profile_id}: online 类型需要 secret_ref（如 env:OPENAI_API_KEY）")
        if self.type == "local" and not self.model:
            raise ValueError(f"profile {self.profile_id}: local 类型需要 model")
        return self


class EmbeddingConfig(BaseModel):
    active_profile: str | None = None
    profiles: list[EmbeddingProfile] = Field(default_factory=list)

    def active(self) -> EmbeddingProfile:
        if not self.profiles:
            raise ValueError("registry 未配置任何 embedding profile")
        if self.active_profile:
            for profile in self.profiles:
                if profile.profile_id == self.active_profile:
                    return profile
            raise ValueError(f"active_profile 不存在：{self.active_profile}")
        return self.profiles[0]


class StorageConfig(BaseModel):
    lancedb_path: str = ""
    sqlite_path: str = ""
    model_cache_path: str = ""

    def resolve(self, base_dir: Path) -> "StorageConfig":
        def expand(value: str) -> str:
            if not value:
                return ""
            path = Path(os.path.expandvars(value)).expanduser()
            if not path.is_absolute():
                path = base_dir / path
            return str(path.resolve())

        return StorageConfig(
            lancedb_path=expand(self.lancedb_path),
            sqlite_path=expand(self.sqlite_path),
            model_cache_path=expand(self.model_cache_path),
        )


class PrefixRule(BaseModel):
    glob: str
    authority: Authority | None = None
    write: Literal["no", "proposal_only", "allow"] | None = None


class KnowledgeBase(BaseModel):
    kb_id: str
    display_name: str | None = None
    source_type: SourceType
    repo: str
    knowledge_root: str = "."
    index_mode: IndexMode | None = None
    index_ref: str = "main"
    serve_refs: list[str] = Field(default_factory=lambda: ["main"])
    allow_working_ref: bool = False
    default_authority: Authority = "note"
    default_write: Literal["no", "proposal_only", "allow"] = "no"
    vector_enabled: bool = True
    include: list[str] = Field(default_factory=lambda: ["**/*.md"])
    exclude: list[str] = Field(default_factory=list)
    prefix_rules: list[PrefixRule] = Field(default_factory=list)

    @field_validator("kb_id")
    @classmethod
    def _kb_id_pattern(cls, value: str) -> str:
        if not value or any(ch for ch in value if not (ch.isalnum() or ch in "-_")):
            raise ValueError("kb_id 只能包含字母、数字、下划线和连字符")
        return value

    @model_validator(mode="after")
    def _defaults_by_source(self) -> "KnowledgeBase":
        if self.index_mode is None:
            self.index_mode = "git-object" if self.source_type == "embedded" else "filesystem"
        if self.source_type == "embedded" and self.index_mode == "filesystem":
            raise ValueError(f"{self.kb_id}: embedded 知识库必须使用 git-object 索引")
        return self


class Registry(BaseModel):
    version: int = 1
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    knowledge_bases: list[KnowledgeBase] = Field(default_factory=list)

    def kb(self, kb_id: str) -> KnowledgeBase:
        for kb in self.knowledge_bases:
            if kb.kb_id == kb_id:
                return kb
        known = ", ".join(kb.kb_id for kb in self.knowledge_bases) or "<空>"
        raise KeyError(f"未知 kb_id：{kb_id}（已注册：{known}）")

    def require_storage(self) -> None:
        if not self.storage.lancedb_path or not self.storage.sqlite_path:
            raise ValueError("registry.storage 必须配置 lancedb_path 与 sqlite_path")


class RegistryContext:
    """注册表 + 所在目录，用于解析相对路径。"""

    def __init__(self, registry: Registry, base_dir: Path):
        self.registry = registry
        self.base_dir = base_dir

    def kb(self, kb_id: str) -> KnowledgeBase:
        return self.registry.kb(kb_id)

    def repo_path(self, kb: KnowledgeBase) -> Path:
        path = Path(os.path.expandvars(kb.repo)).expanduser()
        if not path.is_absolute():
            path = self.base_dir / path
        return path.resolve()

    def scan_root(self, kb: KnowledgeBase) -> Path:
        return (self.repo_path(kb) / kb.knowledge_root).resolve()

    def partition_of(self, kb: KnowledgeBase, repo_rel: str) -> str:
        root = Path(kb.knowledge_root).as_posix().strip("./")
        prefix = f"{root}/" if root else ""
        rel = repo_rel[len(prefix):] if prefix and repo_rel.startswith(prefix) else repo_rel
        return rel.split("/", 1)[0] if "/" in rel else ""

    def rule_for(self, kb: KnowledgeBase, repo_rel: str) -> PrefixRule | None:
        matched: PrefixRule | None = None
        for rule in kb.prefix_rules:
            if Path(repo_rel).match(rule.glob):
                matched = rule
        return matched


def registry_file() -> Path:
    return Path(os.environ.get("KB_CONFIG", DEFAULT_REGISTRY)).expanduser()


def load_registry(path: Path | str | None = None) -> RegistryContext:
    file = Path(path).expanduser() if path else registry_file()
    if not file.exists():
        raise FileNotFoundError(f"注册表不存在：{file}（先运行 kb-gateway init 或设置 KB_CONFIG）")
    raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    registry = Registry.model_validate(raw)
    registry.storage = registry.storage.resolve(file.parent)
    return RegistryContext(registry, file.parent.resolve())


def load_raw(path: Path | str) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def save_raw(path: Path | str, data: dict) -> None:
    Path(path).write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

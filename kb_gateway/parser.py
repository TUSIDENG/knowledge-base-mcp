"""Markdown 解析：front matter 与正文分离。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml


@dataclass
class ParsedMarkdown:
    front_matter: dict[str, Any] = field(default_factory=dict)
    body: str = ""


def parse_markdown(text: str) -> ParsedMarkdown:
    """解析 YAML front matter；格式非法时保留原文，不让单个文档阻断索引。"""
    text = text.lstrip("\ufeff")
    if not text.startswith("---"):
        return ParsedMarkdown(body=text)

    lines = text.splitlines(keepends=True)
    if lines[0].strip() != "---":
        return ParsedMarkdown(body=text)

    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() in {"---", "..."}:
            end = index
            break
    if end is None:
        return ParsedMarkdown(body=text)

    raw = "".join(lines[1:end])
    try:
        front = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        return ParsedMarkdown(body=text)
    if not isinstance(front, dict):
        return ParsedMarkdown(body=text)
    return ParsedMarkdown(front_matter=front, body="".join(lines[end + 1:]))


def first_heading(body: str) -> str | None:
    in_fence = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence or not stripped.startswith("#"):
            continue
        return stripped.lstrip("#").strip()
    return None

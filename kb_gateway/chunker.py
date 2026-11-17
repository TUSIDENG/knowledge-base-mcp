"""分块策略：按标题切段，表格保留表头，代码块整体保留。"""

from __future__ import annotations

import re
from dataclasses import dataclass

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
IMAGE_RE = re.compile(r"^!\[(.*?)\]\((.+?)\)\s*$")

# 中英混排的粗略换算：1 token ≈ 3 字符
CHARS_PER_TOKEN = 3
MAX_TOKENS = 512
OVERLAP_TOKENS = 64
TABLE_ROWS_PER_CHUNK = 10
SQL_MAX_CHARS = 800 * CHARS_PER_TOKEN


@dataclass
class Chunk:
    index: int
    content: str
    heading_path: str
    kind: str
    table_header: list[str] | None = None


def _split_fence(line: str) -> str | None:
    stripped = line.lstrip()
    if stripped.startswith("```"):
        return "```"
    if stripped.startswith("~~~"):
        return "~~~"
    return None


def _split_sections(body: str) -> list[tuple[str, list[str]]]:
    """返回 (heading_path, lines)，heading_path 形如 'A > B'。"""
    sections: list[tuple[str, list[str]]] = []
    stack: list[tuple[int, str]] = []
    current: list[str] = []
    in_fence: str | None = None

    for line in body.splitlines():
        fence = _split_fence(line)
        if fence:
            if in_fence is None:
                in_fence = fence
            elif fence == in_fence:
                in_fence = None
            current.append(line)
            continue
        match = HEADING_RE.match(line) if in_fence is None else None
        if match:
            if current:
                sections.append((" > ".join(title for _, title in stack), current))
            level = len(match.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, match.group(2).strip()))
            current = []
            continue
        current.append(line)

    if current:
        sections.append((" > ".join(title for _, title in stack), current))
    return sections


def _windows(text: str, max_chars: int, overlap: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    start = 0
    while start < len(text):
        parts.append(text[start:start + max_chars])
        if start + max_chars >= len(text):
            break
        start += max_chars - overlap
    return parts


def _table_chunks(lines: list[str], heading: str) -> list[Chunk]:
    rows = [line for line in lines if line.strip()]
    if len(rows) < 2:
        return [Chunk(0, "\n".join(rows), heading, "table")]
    header, separator = rows[0], rows[1]
    data = rows[2:]
    cells = [cell.strip() for cell in header.strip().strip("|").split("|")]

    def build(group: list[str]) -> Chunk:
        return Chunk(0, "\n".join([header, separator, *group]), heading, "table", cells)

    if len(data) <= TABLE_ROWS_PER_CHUNK * 2:
        return [build(data)]
    return [build(data[i:i + TABLE_ROWS_PER_CHUNK]) for i in range(0, len(data), TABLE_ROWS_PER_CHUNK)]


def _code_chunks(lines: list[str], heading: str, lang: str, max_chars: int) -> list[Chunk]:
    content = "\n".join(lines).strip("\n")
    if lang.lower() == "sql" and len(content) > SQL_MAX_CHARS:
        pieces: list[str] = []
        buffer = ""
        for statement in content.split(";\n"):
            candidate = f"{buffer}{statement};\n" if buffer else f"{statement};\n"
            if buffer and len(candidate) > max_chars:
                pieces.append(buffer.rstrip())
                buffer = f"{statement};\n"
            else:
                buffer = candidate
        if buffer.strip():
            pieces.append(buffer.rstrip())
        return [Chunk(0, piece, heading, "code") for piece in pieces]
    return [Chunk(0, content, heading, "code")]


def _paragraph_chunks(
    lines: list[str], heading: str, max_chars: int, overlap: int
) -> list[Chunk]:
    text = "\n".join(lines).strip()
    if not text:
        return []
    return [Chunk(0, part, heading, "paragraph") for part in _windows(text, max_chars, overlap)]


def _blocks(lines: list[str]) -> list[tuple[str, list[str], str]]:
    """把段落切成 (kind, lines, lang)。"""
    blocks: list[tuple[str, list[str], str]] = []
    buffer: list[str] = []
    fence: str | None = None
    fence_lines: list[str] = []
    fence_lang = ""

    def flush() -> None:
        if buffer:
            blocks.append(("paragraph", list(buffer), ""))
            buffer.clear()

    index = 0
    while index < len(lines):
        line = lines[index]
        marker = _split_fence(line)
        if fence is None and marker:
            flush()
            fence = marker
            fence_lines = [line]
            fence_lang = line.lstrip()[len(marker):].strip()
            index += 1
            continue
        if fence is not None:
            fence_lines.append(line)
            if marker == fence:
                blocks.append(("code", fence_lines, fence_lang))
                fence = None
                fence_lines, fence_lang = [], ""
            index += 1
            continue

        if not line.strip():
            flush()
            index += 1
            continue

        image = IMAGE_RE.match(line.strip())
        if image:
            flush()
            blocks.append(("image", [line], ""))
            index += 1
            continue

        if line.lstrip().startswith("|") and index + 1 < len(lines) and re.match(
            r"^\s*\|?[\s:|-]+\|?\s*$", lines[index + 1]
        ):
            flush()
            table = [line]
            index += 1
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                table.append(lines[index])
                index += 1
            blocks.append(("table", table, ""))
            continue

        buffer.append(line)
        index += 1

    if fence is not None:
        blocks.append(("code", fence_lines, fence_lang))
    flush()
    return blocks


def chunk_markdown(body: str, max_tokens: int = MAX_TOKENS, overlap_tokens: int = OVERLAP_TOKENS) -> list[Chunk]:
    max_chars = max_tokens * CHARS_PER_TOKEN
    overlap = overlap_tokens * CHARS_PER_TOKEN
    chunks: list[Chunk] = []

    for heading, lines in _split_sections(body):
        for kind, block_lines, lang in _blocks(lines):
            if kind == "table":
                chunks.extend(_table_chunks(block_lines, heading))
            elif kind == "code":
                chunks.extend(_code_chunks(block_lines, heading, lang, max_chars))
            elif kind == "image":
                alt = IMAGE_RE.match(block_lines[0].strip())
                caption = alt.group(1).strip() if alt else ""
                target = alt.group(2).strip() if alt else ""
                chunks.append(Chunk(0, f"![{caption}]({target})", heading, "image"))
            else:
                chunks.extend(_paragraph_chunks(block_lines, heading, max_chars, overlap))

    result = [chunk for chunk in chunks if chunk.content.strip()]
    for position, chunk in enumerate(result):
        chunk.index = position
    return result


def chunk_plain(text: str, max_tokens: int = MAX_TOKENS, overlap_tokens: int = OVERLAP_TOKENS) -> list[Chunk]:
    max_chars = max_tokens * CHARS_PER_TOKEN
    overlap = overlap_tokens * CHARS_PER_TOKEN
    parts = _windows(text.strip(), max_chars, overlap) if text.strip() else []
    return [Chunk(index, part, "", "plain") for index, part in enumerate(parts)]
